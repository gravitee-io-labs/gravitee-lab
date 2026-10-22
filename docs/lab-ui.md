# Lab UI

The Lab UI is a web page that runs the lab for you. It lists playgrounds, setups and add-ons, turns them on and off,
shows what runs and where to open it, and edits the same files you would edit by hand. It runs the Docker commands for
you, mostly `docker compose`.

```bash
make ui
```

It listens on `127.0.0.1:9000`, so only this machine reaches it, and it answers only to the names `localhost` and
`127.0.0.1`. Under another name, such as `0.0.0.0`, the page says `Open the Lab UI at http://localhost:9000`. It opens
on the [Dashboard](#dashboard). A first visit opens *How Gravitee Lab works*, which first asks *Where does API Management
run?* ([Where API Management runs](#where-api-management-runs)), then has a *Start here* block, shows the layers, what Start
and Stop mean and where to go first, and ends with links to the *Source* and to *Troubleshooting*. The info button of the menu
opens it again. It does not open by itself once that was chosen on this machine, in any browser.

Keep port 9000. The platform's consoles and the playground apps let only that address frame them, so with
`UI_PORT=9001 make ui` every UI of a playground shows as refused beside the guide. When another program holds port
9000, `make ui` says so and names `UI_PORT`:
[Port 9000 is taken](troubleshooting.md#port-9000-is-taken-or-the-lab-ui-is-already-running).

## Where API Management runs

The core is the Gravitee platform under everything else: API Management, Access Management, Alert Engine, Gamma and their
databases. Of its products, only API Management can run in Gravitee Cloud; the rest always runs on this machine. Choose where
API Management runs in *How Gravitee Lab works*, on API Management's row of the Dashboard (two icons after its name, *This
machine* and *Gravitee Cloud*) or in the head of the [Gravitee Cloud page](#gravitee-cloud) (*Where API Management runs*): one
setting of this machine, kept in `.lab/core.json`, out of git.

| API Management runs on | What runs where | What *Start all* on the Dashboard does |
|---|---|---|
| *This machine*, the default | Docker runs the whole platform of `docker-compose.yml` here, as the lab always did | Starts the platform and what is on |
| *Gravitee Cloud* | API Management runs in one environment of your Gravitee Cloud account, its hybrid gateway here; the rest of the platform runs here | Starts the hybrid gateway of that environment, the rest of the platform with the services it depends on, and what is on; not API Management's own services |

- **Choosing *Gravitee Cloud* goes where it can be done.** With no account, it opens *Connect Gravitee Cloud*; with an account
  and no environment the Lab is linked to (it keeps a Cloud Token or a hybrid gateway of it), the *Set up* of its first
  environment; with several linked environments, a picker of them. The choice is kept once an environment is linked: until then,
  it stays *This machine*. With several linked environments, the environment picker stands beside the choice.
- **Choosing starts and stops nothing.** The tooltip of each choice says what it changes. Containers, Images and Logs stay as
  they are, and you can switch back at any time: what was left running keeps running until you stop it.
- **What *Start all* leaves out is read from the Compose files.** The services of API Management's group (its consoles, its
  portal, Gamma) do not start, nor a service of the platform that only they depend on (`depends_on`, add-ons included), nor an
  add-on or playground service that depends on one of them. A database that a product of this machine or an add-on uses
  starts. The dialog of *Start all* lists *Starts*, *Serves* (the environment) and *Not started*, with why.
- **It is the place proposed first.** With no place chosen in [Deploy to](#deploy-to), a playground deploys to the chosen
  environment, unless it cannot go there: its page then says why and proposes *This machine*. A new choice forgets the place
  chosen in *Deploy to* before, so that every playground follows it.
- **The welcome dialog follows it.** Its first step says what is started first, and the Core layer keeps every product of the
  platform and says where API Management runs.

## Pages

| Page | What it does |
|---|---|
| Dashboard | The page the Lab UI opens on, first in the menu: is the platform up, which version of each product runs and is there a newer one, how many hybrid gateways your Gravitee Cloud accounts have, what else is on and a search through the catalog, how much the lab uses of the machine. [Details](#dashboard) |
| Playgrounds | Use cases with their apps, sample data and a guide. Each has [its own page](#the-playground-page) |
| Setups | Named sets of add-ons, turned on and off as one |
| Add-ons | One capability each. One that needs a secret shows *Settings* until it has it |
| Catalogs | Where the items come from: add, update, go back to the previous copy or remove a catalog, make a new one, and see what each holds: [Catalogs](catalogs.md) |
| Containers | The containers by group, the platform's actions, and every console to open |
| Logs | Several services merged in time order, filtered by level and by text |
| Images | The version of every component, and the registry of the Gravitee ones: [Images and versions](configuration.md#images-and-versions). A row's *State* is *Running*, *Stopped* (it is on and does not run) or *Off* (what owns it is not turned on: its tooltip names it); *Not pulled* stands beside the version when its image is not on this machine, and the list of versions marks the ones that are with *Pulled*, in one column |
| Plugins | The plugins of each product: [Plugins](configuration.md#plugins) |
| Gravitee Cloud | The Gravitee Cloud accounts connected here, and for each environment the gateway that runs on this machine, whether the Lab may deploy to it and what it deployed; then every hybrid gateway of the account: [Gravitee Cloud](#gravitee-cloud) |

Playgrounds, Setups and Add-ons are lists with a search, a category filter, the options *Running* and *Starred*, which
combine, and, once there is more than one catalog, a catalog picker (*All catalogs*, one catalog, or *Personal*). Each row says the item's state (*Running*, *Stopped*, *Off*...) in a
column of its own, next to its category and, on a wide window with more than one catalog, the catalog it comes from. A row opens on what the item runs and its files, from its chevron or a click anywhere on it.

The menu also holds the state of the platform, the one place it is said on every page (*Platform running*, *Platform
stopped*, *Platform issue*, whose popover says what the issue is, *Docker is not reachable*); while a job runs or once one
failed, the Activity chip with it; the info button, the theme switch (system, dark, light) and a button that collapses the
menu. The version of the Lab stands at the right of its name, at the top: it opens [its versions](#versions-of-the-lab), and turns
into *Update* while a newer one is out (a dot on the logo says it when the menu is collapsed). Collapsed, the menu shows icons, each named in a tooltip.

The banner above the page is a notice for each thing that needs you, most blocking first, each with its icon, a headline,
a short summary and its button at the right: the Lab UI not answering, Docker not answering, the platform stopped (with
*Start*), a license problem (with *Set license*), containers that differ from what is saved (*Apply needed*, how many
containers, and *Apply*; the summary opens the list of what and why), Docker's memory, and any file the Lab UI could not
read, which opens in the editor. Before the Lab UI first answers, the page says it is reading the lab; when it does not
answer at all, the page says so, how to start it (`make ui`) and *Try again*. Once you connected a
[Gravitee Cloud account](#gravitee-cloud), it leaves out the calm *stopped* line while an add-on or a playground runs, because a deploy to
Gravitee Cloud runs without the core.

## On, off and running

An add-on, a setup or a playground has two facts, and the Lab UI shows both:

- **On**: it is part of what runs with the platform. The [on file](#the-on-file) lists what is on.
- **Running**: its containers run.

Its dot says which:

| Dot | Meaning |
|---|---|
| Green | It runs. Partly running is green too: some of its containers were stopped from the Containers page, and its note says which |
| Ring | It is on and does not run. Its note says *Starts with the platform* while the platform is stopped, and *Stopped* while the platform runs |
| Amber, pulsing | It is starting or stopping |
| Red | An issue: a bad exit, out of memory, unhealthy or restarting |
| Gray | It is off |
| Muted check mark | A one-shot container that ended well, such as a migration or a seed |

One that runs without being on (started by a command, not from the Lab UI) says *Running* and *Started by hand*: the platform
does not start it. *Turn on* adds it to what starts with the platform, as its tooltip says.

On the Playgrounds, Setups and Add-ons lists, what is to know of a row is a badge in its State, after its state (as on the
Dashboard), never in its description, which is one muted line under the name (two on a phone) and says what the item is: *Started
by hand*, *Needs a license*, *Needs newer versions*, *Apply needed*, *Needs settings*. A star follows the name. A badge that leads to its fix has an arrow and is amber when it keeps the
item from starting (*Needs a license* opens the license form, *Needs newer versions* the Images page, *Needs settings* the
settings that lack a value, or the add-on that lacks them, *via Kafka Desk* that add-on); hovering or focusing it says
why (the versions it needs, the names of the settings without a value, what of the license it uses) and where it is fixed. One
with an information mark tells more on hover, focus or a tap. A long name is cut before its badges are. An add-on that lacks a
setting keeps its *Start*, which waits for it and says which (hover or focus it), or its *Stop* while it runs; its key button
opens its settings, as the badge does. A button that cannot be used says why when you hover it, focus it or tap it.

An explanation is one look everywhere: a short label is a dark tooltip on one line, anything longer a card under its button
(a title, the sentence, rows with versions and settings' names in monospace, what the action does). You can move the pointer
onto the card, scroll it when it is long, and Escape closes it.

## Start and stop an item

The button of a row says what a click does:

| Button | When | What it does |
|---|---|---|
| Start | It is off, or on and stopped | Turns it on and runs it |
| Stop | It runs | Stops it and turns it off |
| Turn off | It is on and does not run | Turns it off |
| Turn on | It runs without being on (started by a command, not from the Lab UI: *Started by hand*) | Adds it to what is on, so that it starts with the platform. It has no play icon: it starts nothing |
| Settings | An add-on lacks a required setting | Opens its settings |

A row that is on and does not run keeps *Turn off* in its ⋯ menu. A name in a detail leads to that item's row, and the
file and key buttons of a row open its compose file and its settings.

A Start or a Stop does only its item. What the item needs starts with it, and the core services it configures restart.
Anything else a click starts, stops, turns off, keeps or restarts is listed first, as numbered steps. The button then
shows how far the job is.

- **Conflicts**: two add-ons that set the same thing on a core service declare a conflict. Starting one stops the other,
  or turns it off, after saying so.
- **Setups**: a setup is a shortcut. It is on when all its add-ons are, whoever turned them on, and it runs when they
  run. With only some of them on it is off and says *Partly on*. Its Stop turns its add-ons off, except those
  something else that is on uses: it says *Keeps*.
- **Playgrounds**: one is on at a time. Starting another one stops the first, or turns it off.
- **Jobs**: one job runs at a time. The other buttons wait and say *Wait for* the job. The Activity chip, in the menu
  while the job runs, opens its output and a *Cancel* button. An image that is not there yet is downloaded first, and each
  one shows a bar, a percentage and the bytes downloaded of its total; the title of the list adds what all of them come to
  and how fast they go. Once downloaded, an image shows *Extracting* until it is *Pulled*.
- **A failed or canceled job**: the item stays on. Its row says *Failed*, or *Canceled* after a *Cancel*. The last line
  of the command is the tooltip of a failure, and *Show output* in its ⋯ menu opens the whole output. Start it again,
  or turn it off.

## Dashboard

The page the Lab UI opens on, and the first item of the menu. It answers what you ask on opening the Lab, in the order you
ask it: is the platform all right, what is in it and which version, what else is on, how much the lab uses of the machine. Its
head has one button for the platform. Every other action of the platform is on the [Containers page](#containers), and every
name leads to the page that holds the detail. Under the head, two cards stand side by side on a wide window (the Gravitee
platform on the left, what else is on on the right, each as high as its rows) and one over the other on a narrower one; the
resources run across the page under them, one line that you unfold.

- **The head** has the title and, while something runs behind what is saved, the badge *Apply needed* beside it, in whatever
  state the platform is, so the *Apply* button is never there without a reason: [hover the badge, focus it or tap it to read
  why](#apply-needed), a row for each product, with the version that runs and the one that is saved. The state of the platform
  is the menu's, which every page shows. The head offers the one
  button that fits: *Start* while the platform is stopped, *Apply* when something runs behind what is saved, else *Stop*. Restart,
  Update, Remove containers and Reset are on the Containers page. The buttons do what they do there, with the same dialogs and the
  same progress, and the banner above leaves out the Start and the Apply that the head has.
- **Where API Management runs**, two icons after API Management's name on its row: [Where API Management runs](#where-api-management-runs).
  With *Gravitee Cloud*, the row is its environment there: the environment's name in place of the version (a picker when
  several are linked), the state of its hybrid gateway here in the words of the Gravitee Cloud page, and what it lacks (*Create
  gateway*, *Set up*...) or *Open console*. Under it, a muted *This machine* row is API Management's local services: *Not used*,
  its dot saying whether they run, its consoles while they do. The other products' rows are unchanged. The head's *Start all*
  opens a dialog that lists what starts (the hybrid gateway, the rest of the platform with what it depends on, the playground's
  apps, the add-ons that are on), *Serves* with the environment, and *Not started* (API Management, what only it uses, what is
  on and needs it); *Stop all* stops what *Start all* starts and leaves API Management's local services as they are. While the
  environment has no hybrid gateway, *Start all* is disabled and says so.
- **Gravitee Platform.** One row for each Gravitee product: API Management, Access Management and Alert Engine. The dot says
  whether it runs, then its name, the *Version* that runs (the version set on the Images page, muted, while it does not run), the *Status* and the consoles of the product that run. The head of the card has the license in words after its title (*Enterprise
  Edition*, *Community Edition*, or *License not saved* with what the next Apply does), then *Containers*, with how many the
  platform has, which opens the Containers page, and *Change versions*, which opens the
  [Images page](configuration.md#images-and-versions); on a narrow card they go under the title with their words.
    - The status is an issue, how many containers run, or *Apply needed* (hover it for why, and for a newer version when one is
    out), else the update: "Up to date", muted, when the version is the newest of its channel on Docker Hub, "Update available" in amber when
    there is a newer one (hover it for the two versions), and nothing when it is not known (without internet, for one). A stable version is compared with
    the latest stable one and a pre-release with the latest pre-release, so a pre-release is never said to be current because of the
    stable one. Until the numbers are read the version is a spinner, never the version that is saved, which may not be the one that runs.
  - A row says something in the place of the update only when something is wrong or not applied: the culprit of an issue in red,
    "Needs a license" (which opens the form that takes one), "2 of 5 running" in amber while some of its containers do not run, or
    the badge *Apply needed* while the product differs from what is saved: its popover says which version runs and which one is
    saved, so the version beside it is read for what it is.
  - The consoles are a button each: API Management has the Gamma Console and the Developer Portal, Access Management the AM
    Console. The consoles that a service marks as older are in one *More* menu. In a card that is not wide enough for them, each
    product has one *Open* button instead (with the mark of a link out, and a chevron when it opens a menu): the console itself when
    it has one, else a menu of them all. The tools of an add-on and the links of a playground are not here.
- **Gravitee Cloud** is the last row of the platform's card, on its columns: its name, your accounts (muted), then how many hybrid
  gateways they have (*No gateway*, *1 gateway*, *2 gateways*). Hover the number, or Tab to it, to read them: each gateway under its
  environment, with its state in the words of the [Gravitee Cloud page](#gravitee-cloud) (*Running here*, *Connecting*, *Stopped here*,
  *Connected elsewhere*, *Not connected*...); click it to open that page, where their environments and their actions are. It has no
  version: Gravitee Cloud updates itself.
  - With no account connected the row says *No account connected* and has a *Connect account* button, which opens the dialog that
    connects one, then sets up a first environment: [Use Gravitee Cloud with the Lab](cloud.md). When a hybrid gateway runs on this
    machine, the row says so too, so that it never says Cloud is not connected while one is syncing from it. An account that cannot
    be read says its state (*Account Token refused* in red, *Unreachable* in amber), why in its popover, and what to do: a token that
    is refused is replaced (*Replace Account Token*), an account that does not answer is asked again (*Check again*). While the
    accounts are read the row says *Loading*. The Cloud is asked when the page shows and never holds the rest up; an account read
    less than 30 seconds ago is not asked again, whether you come back to the page or to the tab. A gateway of this machine that has
    not connected yet is asked again after 5 seconds, then 10, 20, 40 and 80, then left as it is.
- **Playgrounds, setups and add-ons.** What is on, then what you starred: a playground, a setup or an add-on on two lines, its
  name (which leads to its page or its row, where its consoles are), and under it its state and what the list says of it as
  badges (why it cannot start, that it started outside the Lab UI, the settings it needs, what runs behind what is saved); then
  its *Start*, *Stop* or *Turn on*, the lists' own button, and *Settings* when the item has some, each in a place of its own
  that stays when another is missing. The state is a dot and a word for every item, the same as everywhere else: *Running*,
  *Starting*, *Stopping*, *Stopped*, *Off*. A badge too long for its line is cut, its popover and the item's page saying it in
  full. A star is turned from here. It has the search and the filters of the lists, and
  keeps them as they do: the search looks through the whole catalog, in the same words as the lists' (name, description, category,
  tags and the members of a setup), so this card is also where you find an item and start it. *Kind* keeps one kind
  (*All kinds*, *Playgrounds*, *Setups*, *Add-ons*, each with how many there are), the options *Running* keeps what runs and
  *Starred* what you starred, together or apart. A long list is paged, and a search that finds nothing says so. With nothing on, a button leads to the playgrounds. The head of the
  card has *Catalogs*, with how many there are, which opens the Catalogs page.
- **Resources.** What the lab's containers use of your machine. By default it is one thin line: *CPU* and *Memory* in
  words, live, and the versions of Docker and Docker Compose at its end when the window has room. Click it, or Tab to it and press
  Enter, to unfold it; your browser remembers the choice. Unfolded, it is a strip of sections: *CPU*, *Memory*, *Storage* and
  *Docker*, each its label over its figure. *CPU* is the load of the containers as a percentage of the cores Docker
  has and *Memory* what they use of the memory Docker has ("4.1% of 6 cores" and "4.9 GB of 7.7 GB"), each beside the last
  minute as a line that slides as time passes, across the rest of its section, between the top of its scale and zero, which are written beside it
  (the CPU's scale is 10 % until a higher point needs more; the memory's is what Docker has). Hover the chart to read what it covers. A load that stops coming is not kept on screen: the figures are dashes until it speaks again. Only the containers of
  the lab count, not what else Docker runs. Hover the label of *CPU* or *Memory*, or Tab to it, to see the five containers that take
  the most and what each takes, and for the memory what is left of Docker's: it says what to stop to make room. The memory turns amber
  when Docker has less than 15 % of it left, and red under 5 %, before a container is stopped for want of it. *Storage* is what Docker
  keeps, the total of `docker system df`: its images, containers, volumes and build cache, with how much of that is *unused*, meaning no
  container uses it (hover it to read how much of each type is unused). Unused volumes can hold data, so removing one loses it. The icon
  button at its end, *Count storage again*, reads them again. *Docker* says the versions of Docker and Docker Compose.
  On a narrower window CPU and Memory share a line, with Storage and Docker under; on a phone, a section over the other.

### What the Dashboard reads

| What | From | When |
|---|---|---|
| The state of the platform, its parts, what is on and what can be opened | The same state as every page | Always, as it changes |
| The versions of the products and of the catalog | The Compose files, `docker ps` and Docker Hub, read by the code of the Images page | The page opens, and again when what runs changes. Docker Hub is kept for an hour |
| The environments of the Cloud | The Gravitee Cloud page's own reads, one call to the Cloud for each account | The page shows, and the tab is seen again |
| The versions of Docker and Compose, the cores and memory Docker has | `docker info` and `docker compose version` | The page opens |
| What Docker stores and what is unused: images, containers, volumes and build cache | `docker system df`, which takes about 2 seconds | The page opens, and *Count storage again* |
| The load and memory of the containers | One `docker stats` process | While the page is in view, folded or not |
| The memory and cores of the machine, the disk | The system itself, with no Docker | The page opens |

- **Docker's answers are kept for a minute.** Going to another page and back reads nothing again. *Count storage again* reads
  `docker system df` again at once and keeps that answer for a minute. A read that failed is tried again after 10 seconds.
- **The load is live.** The Lab UI starts one `docker stats` process when the page connects and stops it when the last
  page that listens leaves. A tab you hide, or a page you leave, closes its connection, so the Lab UI in the background
  costs Docker nothing. A connection that the browser gave up on is opened again after 3 seconds, then 6, 12 and so on up to a minute. The CPU is the sum of the lab's
  containers' CPU divided by the cores Docker has, so 100% is every core it has. The last minute is drawn by the page, on a scale that never changes (the line grows in from the right, then passes) and kept nowhere, and only while Resources is unfolded. With *reduce motion* set in
  your system, the charts are drawn when a sample comes and do not slide.
- **The latest versions never hold the page.** It shows what it has, asks again every 3 seconds while Docker Hub is
  still answering, and fills them in. Without internet they show a dash.
- **Without Docker**, the banner says why, once, and the menu's chip says it is not reachable. The figures that need it,
  CPU, Memory and Storage, are dashes and the charts are gone. No dot is colored, no count is said, the buttons wait and say
  why, and there is nothing to open. The page still shows what needs no Docker: the versions configured for the products, the
  Cloud, what is on, and the disk.

## Containers

The Containers page lists the containers by group, under the band of the platform (*Gravitee Platform*), which folds: its groups
(API Management, Access Management, Alert Engine, Databases), then under a band of their own, which folds too, the add-ons of the
platform (the ones in the *Gravitee Platform* category) that are on or still run. Below it, the other add-ons that are on or still run
(*Other add-ons*: Kafka, Jade…), then the playground. A band stays open while one of its groups is in an issue. Each row shows the group with what the Lab notes
of it as a badge after its name (*Apply needed*, *Needs a license*, *Started by hand*), its state (a dot and a
word: *Running*, *Partly running*, *Stopped*), its version, the ports it publishes (the first, and how many more, all of
them in its tooltip) and how long it has run whole (the uptime of its youngest container), with buttons to open it, read its
logs, restart it, and stop or start it. Opened, it lists its containers, each with its own state, version, ports and uptime; a
version is said without the system its image is built on (`-debian`), the whole tag in its tooltip, and the one saved there too
when it differs, its group's badge saying why. A product's row has a plug button that opens its plugins, and the name
of a playground or an add-on opens its page or its row. The *Consoles* card lists every console, each with its port and the path of its address. The primary
button of the head, *Start* or *Apply*, is its last, before its menu.

The *Gravitee Platform* line shows the license as a pill in words (*Enterprise Edition* and its tier, *Community Edition*, or
*License not saved* in amber while the containers still run a license that is no longer saved: the next *Apply* or restart turns
Enterprise features off), which opens what the license gives, its tooltip its expiry, and has the buttons for the core's file and settings. Without a license the Lab UI does not start the Alert Engine, and its row says *Needs a license*: see
[Alert Engine keeps restarting](troubleshooting.md#alert-engine-keeps-restarting).

The head acts on containers, and what is on stays on:

| Button | What it does |
|---|---|
| Start | Runs the platform and everything that is on, after saying what it starts beyond the platform |
| Stop | Stops every container. They stay stopped when Docker restarts. A container that crashes starts again by itself |
| Restart | Restarts what runs, as it is |
| Update | Pulls the images, then recreates the containers that run and changed. What was stopped stays stopped |
| Apply | Recreates the containers that do not run what is saved |
| ⋯ → Remove containers | Removes containers and networks, keeps the data volumes |
| ⋯ → Reset | Deletes every container and volume, and turns off the playground that is on. You type `reset` to confirm |

A group, a container, or the containers of an add-on or a playground start, stop and restart on their own line. The
item stays on or off.

Once a [Gravitee Cloud account](#gravitee-cloud) is connected, the ports of a container have a tooltip with the address another container
of the lab uses to reach it, such as `In the lab: smtp4dev:80`. That is what a backend URL in Gravitee Cloud takes.

## Apply needed

A version saved on Images, a change saved in the editor and a new license reach the containers that run once you apply
them. Until then, the row, each group of the Containers page, each product of the Dashboard and the Images page, and the banner show the badge *Apply needed* with a small information mark. The banner, which has the *Apply* button, is on every page but Containers, whose table already says it and whose head has *Apply*; no page has the badge beside its title. On Images and on Plugins the head's *Apply* is for the changes chosen there. Hover it, focus it with the keyboard or tap it to open a card that says
why: a table of the version that runs and the one that is saved (under *Running* and *Saved*; a version saved that is older than
the one that runs is amber, as *Apply* goes back to it), that an
image under the same tag changed since the container started, or which settings changed since it started, their names in
monospace and never their values (or, when only what Compose sets for the container changed, that its configuration did).
The badge of the head and the summary of the banner open a card with how many containers differ from what is saved, a row for each
product, add-on or playground with its causes (the services a cause is about when it is not all of them), and what *Apply*
does. When the cause cannot be told, the card says that the configuration changed. You can move the pointer onto the card
to read it at leisure, and Escape closes it.

- *Apply* recreates the containers that do not run what is saved. It starts nothing that was stopped. Its dialog says what it
  recreates, and, in red, what goes back to an older version than the one that runs (data a newer version wrote may not open in
  it) and what loses the license it runs when none is saved: check them before you confirm.
- *Save & apply*, in the editor and on Images, saves first. It says which containers it recreates before it does. With nothing
  unsaved the button is *Apply*, and the badge *Apply needed* beside the page's title says why.
- With nothing running, a change is applied at the next start.

## Logs

Pick the services, and Logs follows them live. Before you pick any, the page says so and *Choose services* opens the picker.

- **Services**: *All running*, *Gravitee only*, *None*, or the ones you tick. They are in sections: each group of the platform,
  then the playground and the add-ons that are on or run. The services of an add-on that runs a gateway of a product are in that product's one section, after its own, under a title of their own: *Hybrid gateway (Acme Cloud, Development)*, the add-on's name and the account and the environment it serves when a gateway runs here for one. It is still an add-on, turned on and off from its row.
- **Lines**: the last 100, 500, 2,000 or 5,000 lines of each service load first.
- **Search**: plain text, or a regular expression (`.*`), with *Aa* for case and *Only matches*. A pattern that is not a valid
  regular expression says so under the search.
- **Levels**: *Error*, *Warn*, *Info*, *Debug* and *Other*, each with its count.
- **Controls**: *Follow*, *Wrap*, *Time*, pause, clear and download.

A container that starts again is followed again, after a *Restarted* line. The page keeps the latest 30,000 lines and
draws the latest 4,000. The stream is open while the page is shown: leave it, or hide the tab, and it closes; come back and
the last lines of each service load again.

## Create, share and star

*New playground*, *New setup* and *New add-on* create one. Choose how and where in the dialog:

- **Form** writes the compose file for you. **AI agent** has an agent write it: say what it should do, then open Claude (the
  app or Claude Code), Codex or Cursor with the prompt written and not sent, or copy the prompt for another agent. The
  agents that take a folder open on the Lab's.
- **Personal** keeps it on this machine, in `.lab/catalogs/local/`, which git ignores, with the id `local/<name>`. A catalog that
  is a folder of yours keeps it there instead, to edit as you like.

A personal item has a *Personal* tag and a ⋯ menu with *Share* and *Delete*. A setup also has *Duplicate*, and *Edit*
when it is personal. Every list shows an item's catalog in its *About*, and can be filtered by catalog.

To share a personal item:

1. Try it from the Lab UI. A playground is shared once it has started well here, its seed ended without an error included.
2. Choose *Share*. The dialog lists the catalogs you can write to and says what keeps it from going to each: it includes a
   personal item (share that one first, then *Use the catalog version* of it), its name is taken, a rule of the catalog's
   standard finds something, or it has not run yet.
3. A catalog that is a repository gets a branch, `lab/<name>-<date>`, pushed with your own git and your name, and the
   dialog gives the link to open the pull request. A catalog that is a folder gets a copy.
4. Once the catalog has the item and you have updated it, *Use the catalog version* replaces your personal copy and keeps
   your settings and your star.

A playground's copy carries the versions it ran on as its `tested` set, unless its file declares some. An item of a catalog
never includes a personal one, so it runs on any machine. An item that another item includes cannot be deleted: its menu says
*Used by*.

The star next to a name keeps an item at hand. *Starred* shows only those. Stars belong to this machine, in
`.lab/starred.json`.

### With an AI agent

The skills `new-addon`, `new-setup`, `new-playground`, `new-catalog` and `share-item` are in `.agents/skills/`. Open the project's
folder in your agent, say what you want, or paste the prompt from the dialog. To call a skill by its name:

| Tool | What to type |
|---|---|
| Claude Code, GitHub Copilot, Cursor | `/new-addon`, then what it should do |
| OpenAI Codex | `$new-addon`, then what it should do |
| Gemini CLI | Plain words: "Use the new-addon skill to..." |

What the agent writes shows in the Lab UI by itself, because the Lab UI reads the folders.

The guides are the `AGENTS.md` files, which OpenAI Codex, GitHub Copilot and Cursor read as they are. Claude Code reads
`CLAUDE.md` and `.claude/skills/`, and Gemini CLI reads `.gemini/settings.json`, which both lead to the same files.

## Files and settings

The file button of a row opens its compose file in an editor. The editor checks the file with `docker compose config`
before it saves, marks an error on its line, and lists the files that go with it. A guide opens with *Code* and
*Preview*.

The key button opens the settings of a `.env` as a form, with *Secrets* and *Settings* sections. Each field shows the
comment that describes it in the compose file, and *Required* when it has no default. See
[Images and versions](configuration.md#images-and-versions) for how the form is built.

## Gravitee Cloud

The Gravitee Cloud page (`#/cloud`; the old `#/platforms` leads there), under **Configure**, shows the Gravitee Cloud accounts connected to this lab. The lab's own
platform is the one the pages call *This machine* wherever you choose a place. Connecting an
account adds places to deploy to, and only what this page and [Deploy to](#deploy-to) list: with none connected, the lab is
as it always was. The whole path, from the Account Token to a call, is in [Use Gravitee Cloud with the Lab](cloud.md).

With no account, the page says *No account connected* and offers *Connect account*. With one or more, each is a card,
and the head of the page has *Connect account*. The head has [Where API Management runs](#where-api-management-runs) too, with
the environment picker when several environments are linked.

**The card.** Its title is the name of the account, with where its console is under it, and its state as a pill:
*Connected*, *Account Token refused* (Cloud refuses the Account Token) or *Unreachable*, *Loading* before the first answer
and *Checking* while you ask again. *Open console* opens the Cloud console of the account in a
new tab: there is none for one environment, because the Lab knows no address for it. The key beside it, *Manage connection*, shows
how the Lab reaches the account: its name, its Account ID (with a button that copies it), its address and whether its Account Token
is set (*Set*, or *Refused*; the token itself is never shown), with *Replace Account Token*. *More* has *Rename*, *Check again* and
*Remove account*. An account that cannot list its environments says so in a notice under the head:
*Environments cannot be listed*, the reason, and the one thing that changes it: *Replace Account Token* (red) when Cloud
refuses the token, *Check again* (amber) when it does not answer. An account that answered before and does not now keeps its
table under the notice, which then says *Showing what was read before*, and is read again every 30 seconds. `#/cloud/<id>` brings a card into view.

**The environments** are one table, the ones you use first under *In use*, the others under *Not set up*:

| Column | What it says |
|---|---|
| Environment | Its name |
| Gateway | On this machine: the state of its [hybrid gateway](cloud.md#create-a-hybrid-gateway), a dot and a word. Its popover has its name, its version and, while it runs here, the address to call |
| Deploys | Whether the Lab may deploy playgrounds into it, a dot and a word: what the Lab deployed ("Acme Shop API deployed", "2 playgrounds deployed"), else that the Cloud Token expires soon (in amber), else the state of the token. Its popover has the rest: the token's state and expiry, what each playground created |
| (the last one) | The one action to take, then *More* |

An environment is in use when it holds a hybrid gateway (the Lab's, one that is connected, or any other, so that it can be
removed), a gateway Cloud deleted while it ran here, a Cloud Token of the Lab's, or something deployed in it. A line of the
others is a name and *Set up*; with more than three of them, the first two show and *Show N more environments* lists the
rest. Each cell is one line, as on the Dashboard, so that both pages draw an environment alike. The terms are explained by the
info icon next to their column, which the keyboard reaches.

- **Gateway** reads *Running here*, *Stopped here*, *Starting*, *Connecting* (it runs and Cloud has not seen
  it yet; *Not connected* after 2 minutes), *Connected elsewhere* (another machine runs it, named in its popover),
  *Removed in Cloud* (it was deleted there while it runs here) or *Not created*. The add-on's own warnings replace these, with
  an amber dot, never the green of what runs: *Needs a license* (and no gateway can start), *Needs settings* (the settings that
  have no value are named in its popover), *License expired*, *Failed* (red), *Apply needed*.
- **Deploys** reads *Can deploy* (a valid Cloud Token and a gateway here), *Needs a gateway* (*Cloud Token set* when the
  gateway's own column already says none is created), *Needs a Cloud Token*, *Cloud Token expired* or *Cloud Token refused*;
  *Expires in 5 days* in amber in its place when the token ends within 14 days; or what the Lab deployed there.
- **The action** is the first that applies: *Add license* (*Replace license*), *Settings* (the add-on's settings, when they
  lack a value), *Create gateway* (*Replace gateway* for a
  gateway of another machine or one Cloud deleted) or *Start gateway* (the add-on's own action, which shows the same
  progress as the Hybrid gateway row of **Add-ons**), then *Add Cloud Token* or *Replace Cloud Token*, then *Stop gateway*.
  *Set up* is for an environment that is not in use. A gateway made here is started at once, through the same dialog as a
  Start, which says first when it stops another environment's gateway.
- **More** has the rest, and is not there when there is none: *Stop gateway*, *Show logs*, *Copy address*, *Add Cloud Token* or
  *Replace Cloud Token*, then what removes, in red, each with what it does on its line: *Remove Cloud Token* (*Deploys stop*),
  *Remove gateway* (its name; its dialog is titled *Remove gateway of Development*, and says when another machine is connected to
  it) and *Stop using Development* (*Removes its token and gateway*), which does both with one confirmation. An environment with
  several gateways says how many after the state of its own (*2 gateways*): click it to see them in the list below.

**The hybrid gateways** of the account are listed under its environments, every one of them, the one that runs on this machine first,
then the connected ones, then the others: its name, its environment, its state (the words of the Gateway column, and *Not connected*
for one that nothing runs), its version and the domains it answers on. *Stop gateway* is on the one that runs here, *Start gateway* on
one whose token the Lab keeps (it takes the place of the one that runs here, after the same dialog), and *More* has *Show logs* and
*Copy address* while it runs here, and *Remove gateway*. With none, the list says *No hybrid gateway* and offers to set up the first
environment.

*Connect account* opens *Connect Gravitee Cloud*, one large dialog of two steps, *Account* then *Environment*, which stays open
from one to the next: the steps are in its head, the current one filled and the one done checked. At the left of each step is
what to do in the Cloud console and the fields; at the right a picture of the console, numbered as the lines at the left (under
860 px it goes under the fields, and on a phone it opens with *Show where to find it*). One footer has the buttons, and a refusal
at their left. Once *Connect* succeeds the page shows the account behind the dialog, which goes on to the environment: *Set up*,
or *Skip for now* (the account stays connected however the dialog closes). An account with no environment, or whose environments
cannot be read, ends there, with a message. The other forms are short dialogs. What each asks:

| Form | Fields |
|---|---|
| *Connect Gravitee Cloud*, first step | *Open Gravitee Cloud* and, under it, *No account? Register for free*; the two lines of the picture (generate an Account Token and copy it; copy the Account ID at the top of the same page); then Account Token and Account ID, each with an information mark on its label whose popover says what it is and where Gravitee Cloud shows it (Settings, Account Tokens). Both are needed: an Account Token is a UUID that carries no claim, and Cloud answers no call that would say whose it is ([why](cloud.md#why-the-account-id-is-asked)). An Account ID that is no UUID is said under its field. *More options* holds the URL, already filled in with Europe's, which *Open Gravitee Cloud* follows. The account's name is read from Cloud (*Rename* in the card's menu changes it) |
| *Connect Gravitee Cloud*, second step | The environment (Development first), then the two parts of *Set up* below |
| *Replace Account Token* | The new token, checked against the Account ID the Lab kept; the popover of its label says where to create it. The record, the Cloud Tokens and the gateways stay |
| *Set up Development* (*Set up* of a row) | The second step of the same dialog, for that environment alone. Two parts you may leave out, their fields shown while ticked: *Run a gateway here* (Name, *Gravitee Lab - Development* by default) and *Allow deploys* (*Open Gravitee Cloud*, the two lines of the pictures *1 Generate* and *2 Choose the environment*, which a segmented control above them chooses, then the Cloud Token). The token is kept first, because it is checked before it is kept, then the gateway is created and started. A part that was done stays done when the other is refused, and closing the dialog still shows it on the card |
| *Create gateway for Development* | Name, and *Create and start*. A line says when another gateway runs here and this one stops it. From *Before you start* it is *Create hybrid gateway for Development* and *Create*: the playground's Start starts it |
| *Add Cloud Token for Development*, *Replace Cloud Token for Development* | Cloud Token, the popover of its label saying where to create it |

A refusal is said in the form, in the server's words. The Lab keeps the tokens in the `.env` files, which git ignores, and
never shows one again. It never asks for the token of a hybrid gateway: it creates the gateway, keeps the token and
writes it in the settings of the Hybrid gateway add-on.

- **One hybrid gateway runs here at a time.** *Start gateway* on an environment whose gateway is not the one that runs swaps
  it, after a dialog that says which environment it *Serves* from now on and which it *Stops serving*. *Stop gateway* is on
  the one that runs. These are the add-on's own actions, so the Hybrid gateway row of **Add-ons** shows the same job.
- **Remove** asks first. A gateway is removed in Cloud, and the add-on is turned off if it ran here. A Cloud Token is
  forgotten on this machine and *Stays there*. An account is forgotten with its tokens and what the playgrounds recorded
  there; its gateways and the APIs they created *Stay there*.
- A gateway that runs here and has not connected is read again every 5 seconds for a minute, every 10 for another, then every 30,
  and reads *Not connected* after 2 minutes, while the page is open.
- **Keyboard**: every control is a button or a link, and a redraw leaves the focus where it was. The explanations of the terms are
  tooltips that the keyboard reaches.
- **Without an account**, the Hybrid gateway add-on reads *Needs settings* like any add-on that lacks a secret. With one,
  the add-on reads *Needs a hybrid gateway*, a link to this page.

### Without a license

The License dialog of a Lab with no license says *Community edition* and offers two ways forward as two cards of one height: *Use Gravitee Cloud*, whose *Connect my account* opens the form that connects an account (it asks whether there is one already and offers a trial of 14 days if there is not), and *Run it on this machine*, whose *Request a license* leads to the self-hosted trial of gravitee.io. `GRAVITEE_EMPLOYEE` in `.env` hides the cards.

## The playground page

The Playgrounds page lists the playgrounds, with search and categories. *Open* on a row opens its page in any
state. *Start* and *Stop* on the row do what they do on the page. A playground that runs without being on (started by a
command) offers *Turn on*, a plain button with no play icon, on its row and in the head of its page.

### Before it starts

The page says what the playground is, what runs, and, under *Before you start*, what Start will do and what needs
attention:

| Group | What it holds |
|---|---|
| Needs your attention | What will not work by itself |
| Done for you at start | What Start does for you |
| Already in place | What is fine already |

The lines cover:

- **The platform**: *Created at start*, *Started at start* or *Running*.
- **Add-ons**: *Turned on at start*, *Started at start*, *Running*, or *Needs settings*, which opens them. An add-on
  that conflicts says *Stops at start* or *Turned off at start*. The core services it changes say *Restarts at start*.
- **Its apps**: built and started.
- **Image versions**: a version older than the playground needs says *Current: X*, and links to the Images page.
  A playground can need pre-release builds of API Management and Access Management.
- **Plugins**: installed, or installed at start from download.gravitee.io.
- **The license**: named for what needs it (the packs of the license, such as *Enterprise authorization engine,
  Observability*), with *Needs a license*, *License expired*, *Not in the license* or *Apply needed* under it, and a *Set
  license* button. Start still runs without it, and says so under its button: *Starts without what needs the license*.
- **Docker's memory**, when it is short.
- **Host prerequisites**, such as Ollama with a model: a missing one warns, with the fix. A check is an http or https
  address, called from your machine.
- **A place besides This machine**: with a [Gravitee Cloud account](#gravitee-cloud) connected, the lines follow the place that *Deploy
  to* shows: [Deploy to](#deploy-to).

The checks are read again when the window comes back, so what you fixed meanwhile shows.

A warning does not block Start: the part of the guide that needs the missing piece will not work. A required setting
missing on one of its add-ons does block it, so does an image version older than the playground needs, and so does a
license that everything of it needs (*Needs a license*, *License expired*, *Not in the license*); a license needed for
one step only blocks nothing. The button
says why. For the images, it names every one that is too old in one sentence, such as *Needs Access Management 4.13 or
later and API Management 4.13.0-alpha.5 or later: choose them on the Images page*, so one visit to Images is
enough. The guide is readable before the first start.

### What Start does

Start sets the playground up in one job, shown in phases:

1. **Add-ons**: the playground goes first in the [on file](#the-on-file), and it includes its add-ons and setups. What
   conflicts with them is turned off.
2. **Plugins**: the missing ones are downloaded.
3. **Apps and services**, or **Platform, apps and services** when the platform starts too: its apps are built and
   started, with what they and the seed need. The core services it changes restart.
4. **Seed**: its output is followed until it ends. A seed that fails fails the start, and the reason is the last line it
   printed. Start again to retry.

Once the seed has recorded what it created, Start only runs again what does not run, for example after a Stop on the
Containers page. It builds nothing and seeds nothing. If the seed recorded nothing, or failed, Start sets the playground
up again.

### The guide and the UIs

A playground that is on shows its guide beside its UIs. The page does not scroll: each panel does.

- **Two panels**: the guide on the left, at least 600 px wide, and the UIs on the right, at least 480 px. Drag the
  divider to resize, or use the arrow keys (16 px, 64 px with Shift; Home and End go to the limits). A double click
  puts it back at 600 px. The width is kept in this browser.
- **Rows**: where the page lacks the room for both panels, about 1,200 px of window with the menu collapsed (1,370 px
  with it open), the guide goes over the UIs. Nothing else changes.
- **Hide the guide**: the button at the left of the tabs hides the guide, and the UIs take the whole card; it shows the
  guide again. Whether it is hidden is kept in this browser, for every playground.
- **The guide**: one page per section, with previous and next, a page picker, and its own address
  (`#/playgrounds/acme-shop/4`). It opens on the page you read last. Diagrams are drawn from Mermaid blocks. A button
  with a play icon runs an action in the playground's app.
- **Tabs**: one per console the playground lists, then *Details*. A UI loads when you first open its tab, and stays
  loaded. *Reload* loads the page it is on again (a UI on another port does not let the Lab UI read its page, so it is
  told: the platform's consoles say it, any other loads the address the Lab UI loaded last), *Back to start* loads its start address, and *Open in a new
  tab* opens the address it is on.
- **A UI that cannot show**: the tab says why, with *Open in a new tab*. A UI that does not run says what its service
  is, one that does not answer says so, and it comes back by itself.
- **The terminal**: a playground that names one (`terminal` in its `x-gravitee`) has a shell under the UIs, in one of its containers.
  Drag its divider up or down, or use the arrow keys (16 px, 64 px with Shift; Home and End go to the limits); a double click
  puts it back at 240 px. The button at the right of its bar folds it away and opens it again. Its height and whether it is folded
  are kept in this browser. The shell lives in the container, so it is the same one when you reload the page or open another
  playground and come back, with what it printed last; it is a new one after it ends (press Enter) or when its container is
  recreated. A *Run* button in a block of the guide types the block at its prompt, and opens the terminal if it is folded. It is
  for what the playground's container can do: a shell on this machine is what your own terminal is for.
- **Details**: everything to open in a browser tab of its own, by product; the accounts, with copy buttons and the
  values your settings give ([how](playgrounds.md#accounts-and-secrets)); what needs attention; and the services with
  their state.
- **History**: turning a page of the guide or changing a tab replaces the browser's current history entry. A Back
  inside a UI goes back through that UI's own pages only. It never moves the guide.

The head has *Open* for the first console (named after it), *Start* while the playground does not run, *Re-seed*
and *Stop*. Once a [Gravitee Cloud account](#gravitee-cloud) is connected it also has [*Deploy to*](#deploy-to), before *Start*.

### Re-seed, stop and reset

**Re-seed** runs the seed again and puts back what the playground creates in Gravitee. It waits for what the seed
needs and names the service that does not run, such as *Management API is not running*. It needs the playground to be
on.

**Stop** offers two ways, in a dialog that first lists what else it stops:

- **Keep what it created**: the apps stop, and so do the add-ons only it uses. What it created in Gravitee stays.
- **Delete what it created**: the seed first deletes what it recorded, through Gravitee's APIs, which must run. If that
  fails, nothing else changes. It is offered once the seed has recorded something.

**Turn off** replaces Stop while nothing of the playground runs.

The **Start** of another playground stops the one that runs, and its dialog says so first, with the same two ways for what
that one created: *Keep what it created* (the first) or *Delete what it created*, which runs its seed's delete before
anything stops, as its own Stop would, and refuses in the same cases. A row of a list never says what its Start would
stop: the dialog does, once.

**Reset**, on the Containers page, deletes every container and volume, so what the seed created goes with them. It
turns the playground off and empties what the seed recorded, so the next Start sets it up again, seed included.

### Deploy to

Once a [Gravitee Cloud account](#gravitee-cloud) is connected, the head of every playground has *Deploy to*. A place is *This machine*,
which is what the lab always did, or one environment of an account, written *Development (My Account)*: the environment,
then the account. The containers always run here. The place says where the seed creates the APIs. With no account
connected, none of what follows is drawn and the page is as it always was.

- **The picker** lists *This machine*, then the environments of each account, by account name. An environment that lacks
  something can be chosen, and its muted tail says the first thing: *No hybrid gateway*, *No Cloud Token*, *Cloud Token
  expired*, *Cloud Token refused*. An environment the playground cannot go to is disabled, with the reason as its tail
  (*Needs Access Management*, *Needs API Management 4.13* and the others, judged against what the gateways of that
  environment run: [What an environment runs](cloud.md#what-an-environment-runs)). An account that cannot be used is one disabled option: *Invalid token*,
  *Unreachable* or *Loading*.
- **The choice is one for the lab.** The place you pick on any page is the default of every playground, kept in this
  browser; without one, [where API Management runs](#where-api-management-runs) is. A playground that cannot go to it shows *This machine*. You can change it while the playground runs: only the
  seed moves, never a container.
- **The main button** reads *Start* while the playground does not run. Once it runs, it reads *Re-seed* where the place
  already holds something, and *Deploy* where it holds nothing. *Re-seed* and *Deploy* act on the place shown, and never
  reach another one.
- **Before you start** describes the place. For an environment it replaces the lines about the core with the account
  (*Connected*), the *Cloud Token* (*Valid*, *Not set*, *Expired*, *Refused*) and the *Hybrid gateway* (*None in
  Development*, *Its token is not kept here*, *Not connected*, *Turned on at start*, *Switches to Development*,
  *Running*), plus the playground's add-ons and host prerequisites. A plus button creates the missing hybrid gateway and
  a key button adds or replaces the Cloud Token, in the same forms as the Gravitee Cloud page. What is missing disables
  *Start* and says so in its tooltip.
- **A dialog always opens for an environment**, even when nothing starts, so that the place is named once more where you
  click. It lists what starts and restarts, with the gateway swap's *Serves* and *Stops serving*, and *Deploys to* names
  the environment. It lists no service of the core: a deploy starts, restarts and recreates none, and it runs the seed
  apart. Deploying another playground first stops the one that runs, which *Stops* lists, because one runs at a time.
- **The phases** are **Add-ons**, **Apps and services**, **Gateway**, which waits up to 2 minutes for Gravitee Cloud to
  say the gateway is connected, and **Seed**.
- **Deployed to** is a panel on the page of the playground, on the not-running page after *What runs* and under
  **Details**. It appears only when an account holds something, and lists each place that does, *This machine* first,
  with a summary such as *3 APIs, 1 Application*. The trash icon of a remote row deletes what the seed created there; it
  needs the playground on, and says why when it is disabled.
- **Open** has a group named after each account where the playground deployed, with a link for each environment that
  holds something, to the account's console.
- **Stop** lists each place that holds something: *Keep what it created* has a row *Stays* for each, *Delete what it
  created* a row *Deletes* for each, and is disabled with the reason of the first place that cannot be reached. The
  hybrid gateway is not part of it: it stops from its own line.
- **On the Playgrounds list**, a playground that can go to Gravitee Cloud has a cloud mark, and its note says what the place
  you picked lacks (*Needs a hybrid gateway*, *Needs a Cloud Token*, *Invalid token*, *Unreachable*), leading to the form
  that fixes it. Its *Start* deploys to that place.

## The on file

`.lab/on.yml` lists what is on. Compose reads it with `docker-compose.yml` through `COMPOSE_FILE` in `.env`, which
`make init` adds, and `make init` creates the file. The Lab UI writes it. By hand:

```yaml
include:
  - .lab/catalogs/<catalog>/addons/<id>/compose.yml
  - .lab/catalogs/<catalog>/addons/<other id>/compose.yml
```

A playground comes first in the list. With an empty file, Compose runs the core alone. If the file is missing, run
`make init`. Then run `make all`.

- If you add anything else to the file, the Lab UI leaves it as you wrote it and turns off the Start and Stop of the
  catalog. The banner says so and opens the file in the editor.
- Stop on the Containers page, or Docker restarting, leaves the file as it is. The platform's next Start runs
  everything that is on again.
- An included add-on reads its own `.env` and the core's, and its paths start from its folder. An add-on includes the
  ones it needs, and Compose brings one included twice only once.

## Versions of the Lab

A version of the Lab is a tag `vX.Y.Z` of the repository. The version at the right of the name of the Lab, at the top of the menu,
says which one this Lab is (*dev* for a clone with no version), and opens a dialog: the version and the day it came out, then one
card that says where this Lab stands, with the one thing to do about it:

| Card | Its button |
|---|---|
| *Gravitee Lab X is available*, with the day it came out | *Update now* |
| *Up to date*, with when that was checked | *Check now*: asks GitHub again. The Lab UI also asks at start and every six hours |
| *Could not check for updates*, with why | *Try again* |
| *Updating to X*, while the update runs | None: the dialog follows it to the end |

Under the card, *What's new*: what each newer version changes, one line per pull request with its link, and *All releases*. A
clone of `main` that is past its last version says how many changes it has more; one with no tag is a *Development version*,
with its commit.

**Update now** moves the clone to the newest version (a fast-forward of `main`), runs `make init`, and starts the Lab UI again; the
page reloads by itself and says what is new, once. The containers keep running. The update is only offered where it is safe,
and the card says why otherwise: a Lab that is not a Git clone, a branch other than `main`, files of the Lab changed on this
machine, commits of its own, or a job that runs. In those cases, update from a terminal: [Stop, update,
remove](installation.md#stop-update-remove).

## What the browser needs

The page uses CSS features that need Chrome or Edge 117, Firefox 121 or Safari 16.4, or later. An app of an item can
need a later one, which its README says.

It loads four libraries from the internet when it needs them:

| Library | From | Used for |
|---|---|---|
| marked and DOMPurify | cdnjs.cloudflare.com | Rendering a guide safely |
| Mermaid | cdnjs.cloudflare.com | Drawing the diagrams of a guide |
| Monaco | cdn.jsdelivr.net | The file editor |

Without internet, a guide shows as plain text, diagrams included, and the editor as a plain text area. Everything
else works. See [What needs the internet](configuration.md#what-needs-the-internet).

Keyboard:

| Key | Does |
|---|---|
| `/` | Goes to the search of the page or the dialog |
| Enter or Space | Opens or closes a row that opens |
| `S` | Stars the row in focus |
| Escape | Closes a list that opens (a picker or a menu) and gives the keyboard back to its button, else a dialog |
