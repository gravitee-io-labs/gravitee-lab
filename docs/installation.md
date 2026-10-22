# Install and run

Gravitee Lab runs the Gravitee platform on Docker Compose on your laptop, for local demos and learning. This page is the
whole path: what to install, how to start it, what needs a license, and how to stop, update and remove it.

1. Install Docker (Compose 5.0 or later, with 8 GB of memory for Docker), git, make, Python 3.9 or later and openssl: see
   [Prerequisites](#prerequisites).
2. Clone the repository and run `make ui`: see [Run it](#run-it).
3. In the Lab UI that opens, click **Start all**. The first start takes about 10 to 15 minutes, because it
   downloads about 15 GB of images.
4. Open a console from the **Containers** page.

## Prerequisites

| Need | Details |
|---|---|
| Docker with Compose 5.0 or later | Check with `docker compose version`. Give Docker 8 GB of memory, more for items that run AI models and for several add-ons at once (an item's page says what it needs). |
| Disk space | About 15 GB free. |
| Free ports | The core publishes ports such as 27017 (MongoDB), 9200 (Elasticsearch) and 6379 (Redis) on 127.0.0.1, and the Lab UI uses 9000. Stop a local server that uses one. The full list is in [Ports](configuration.md#ports). |
| One lab per machine | The project is named `gravitee` and its containers `gio-*`, so two clones collide. Stop the lab of another clone first. |
| Python 3.9 or later, git, make, openssl | The Lab UI uses only Python's standard library. |
| A browser | Chrome or Edge 117 or later, Firefox 121 or later, Safari 16.4 or later. An app of an item may need a later one. See [what the browser needs](lab-ui.md#what-the-browser-needs). |
| Internet access | Docker Hub, docker.elastic.co and download.gravitee.io. An item can need more, such as a model host or the registries of the packages it builds. See [what needs the internet](configuration.md#what-needs-the-internet). |

Clone outside folders synced by OneDrive, iCloud Drive or Google Drive: containers write into the clone, and a sync client can lock or rewrite those files.

**Tested on** macOS (Apple silicon) by the maintainer. CI runs the checks and the tests on Ubuntu. Linux and Windows with WSL 2 are expected to work.

### macOS

Install [Docker Desktop](https://docs.docker.com/desktop/setup/install/mac-install/) (`brew install --cask docker-desktop`), then `brew install python` and `xcode-select --install` (git and make). macOS ships openssl.

### Linux

Install Docker Engine and the Compose plugin from [Docker's repository](https://docs.docker.com/engine/install/), because distribution packages are often older than Compose 5.0. Then run `sudo apt install git make openssl python3` (Debian, Ubuntu) or `sudo dnf install git make openssl python3` (Fedora). To run Docker without `sudo`, run `sudo usermod -aG docker $USER` and log in again.

### Windows with WSL 2

The lab runs in WSL 2. PowerShell, cmd and Git Bash are not supported. Items that run AI models through Ollama on Windows are untested.

1. In PowerShell, run `wsl --install` to get an Ubuntu distribution, and restart Windows if it asks ([Microsoft's guide](https://learn.microsoft.com/en-us/windows/wsl/install)).
2. Install [Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/) and turn on WSL integration for your distribution (Settings > Resources > WSL integration), or install Docker Engine inside the distribution as on Linux.
3. In the WSL terminal, install the tools as on Linux.
4. Clone inside the Linux filesystem (`~/gravitee-lab`), not under `/mnt/c`.
5. Run every command in the WSL terminal, and open http://localhost:9000 in your Windows browser.
6. Set the memory in `%UserProfile%\.wslconfig` (`[wsl2]` and, for example, `memory=12GB`), then run `wsl --shutdown`.

[Windows and WSL 2](troubleshooting.md#windows-and-wsl-2) has the details.

## Run it

```bash
git clone https://github.com/gravitee-io-labs/gravitee-lab.git
```

```bash
cd gravitee-lab
```

```bash
make ui
```

`make ui` creates what a fresh clone lacks (`.env`, the demo certificates, the plugin folders and `.lab/on.yml`), starts the Lab UI and opens http://localhost:9000, on its Dashboard. Its first visit checks your machine (Docker, its memory, the disk, the ports, Git) and gives the fix of anything missing: [Your machine](lab-ui.md#your-machine).

In the Lab UI, click **Start all**. The first start pulls the images, about 15 GB: allow about 10 to 15 minutes, depending on your connection. Then open a console from the **Dashboard**. Sign in to the API Management and Gamma consoles with `admin` / `admin`, and to the Access Management console with `admin` / `adminadmin`. Gamma is Gravitee's console for AI agents, MCP servers and LLM traffic, and part of API Management. The other demo logins are in [Logins](configuration.md#logins).

To go further, turn on add-ons, setups and a playground from their pages. An add-on is one extra capability, a setup turns several add-ons on together, and a playground is a use case with its apps, sample data and a guide. [The Lab UI](lab-ui.md) says what each button does.

### Without the Lab UI

The Lab UI runs `docker compose` for you. To do it yourself, use the Makefile (`make help` lists every target):

| Command | What it does |
|---|---|
| `make init` | Create what a fresh clone lacks: `.env`, the demo certificates, the plugin folders and `.lab/on.yml` |
| `make all` | Create and start the core and what is on |
| `make ps` | Show container status and health |
| `make logs SVC=<name>` | Follow the logs, of one service or of all |
| `make stop` | Stop all containers and keep the data |
| `make urls` | Print what is on and the URL of every console |

Run `make init` once before a `docker compose` command that you type yourself: `make ui` and `make all` run it for you. `.lab/on.yml` lists what is on: see [the on file](lab-ui.md#the-on-file).

## What needs a license

| You want | You need |
|---|---|
| API Management, Access Management, Gamma's community features, and every add-on or setup whose row does not say **Needs a license** | Nothing more: without a license you get the Community Edition |
| The Alert Engine | A Gravitee Enterprise license. Without one the Lab UI does not start it and shows **Needs a license** on its row ([details](troubleshooting.md#alert-engine-keeps-restarting)) |
| A playground or an add-on that says it needs more | What its page and its README say under *What you need*: a license, later versions, a host service such as Ollama |
| The Hybrid gateway add-on | A license with the feature `apim-bridge-gateway`, and a Gravitee Cloud account: [Use Gravitee Cloud with the Lab](cloud.md) |

- **A license**: to get one, ask your Gravitee contact, or see [gravitee.io](https://www.gravitee.io). Add it from the **Community Edition** chip of the Containers page: [License](configuration.md#license) has the steps.
- **Later or pre-release builds**: an item can need versions the core does not default to. Choose them on the Images page. The item's page lists what is missing, and **Start** stays off until the versions fit.
- **Ollama**, for the items that use it, runs on your machine, and the containers must be able to reach it. On Linux and WSL 2, bind it to the address of Docker's bridge first. If that fails, use `OLLAMA_HOST=0.0.0.0`, which opens Ollama to your network with no sign-in, so use a firewall. [The steps](troubleshooting.md#ollama-is-not-reachable-from-the-containers).

## Stop, update, remove

- **Stop the Lab UI**: press Ctrl+C in its terminal. The containers keep running.
- **Stop the containers**: run `make stop`, or click **Stop** on the Containers page. The data stays.
- **Update**: click **Update to** in the menu when a newer version is out ([Versions of the Lab](lab-ui.md#versions-of-the-lab)). From a terminal, or for a clone with changes of its own: stop the platform with `make stop`, run `make update` (a fast-forward `git pull`, then the download of the catalogs and the move of your settings if the layout changed: see [Updating a Lab from before catalogs](catalogs.md#updating-a-lab-from-before-catalogs)), then `make ui`. **Update** on the Containers page pulls newer images of the versions you chose.
- **Remove**: run `make reset` to delete the containers, the data volumes and what the seeds recorded (a seed is a one-time job that creates a playground's sample APIs and users; `make reset` asks first), then delete the folder. To start clean and keep the folder, run `make reset` and start again, or use **Reset** in the Containers page's menu, which also turns off the playground that is on. On Linux, containers can leave files that your user cannot delete (in `plugins/` and in the `.state/` folders of the installed playgrounds): if `rm` says "Permission denied", use `sudo rm -rf`. To free the disk, `docker image prune -a` removes every image that no container uses, also those of other projects.

## Help

- Start with [Troubleshooting](troubleshooting.md).
- For a bug or a question about the lab, open an issue in the Issues tab of this repository. Include your operating system, `docker --version`, `docker compose version`, `git rev-parse --short HEAD`, the output of `make ps` and the log lines that matter. Remove your license, tokens and passwords first.
- For a vulnerability, follow [SECURITY.md](../SECURITY.md).
- For questions about Gravitee products, see the [documentation](https://documentation.gravitee.io) and the [community](https://community.gravitee.io).
