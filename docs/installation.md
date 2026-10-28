# Install and run

Gravitee Lab runs the Gravitee platform on Docker Compose on your laptop, for local demos and learning. This page is the
whole path: what to install, how to start it, what needs a license, and how to stop, update and remove it.

1. Run the installer: see [Install](#install). It adds what your machine lacks, then opens the Lab UI.
2. If the Lab UI says Docker is missing or stopped, follow its link: the lab needs Docker with Compose 5.0 or later and 8 GB
   of memory for Docker.
3. Click **Start all**. The first start takes about 10 to 15 minutes, because it downloads about 15 GB of images.
4. Open a console from the **Dashboard**.

## Install

On macOS and Linux, in a terminal, without `sudo`:

```bash
curl -fsSL https://raw.githubusercontent.com/gravitee-io-labs/gravitee-lab/main/install.sh | sh
```

It uses what your machine has, adds only what is missing, then opens the Lab UI in your browser:

1. **Tools**: Git, Make, Python 3.9 or later and OpenSSL. On macOS, it opens Apple's window for its command line tools:
   click **Install**, then **Agree** (about 1 GB). On Linux, it asks, then installs them with the package manager of your
   system (`apt-get`, `dnf`, `yum`, `zypper`, `pacman` or `apk`) through `sudo`. A system whose `python3` is older than 3.9
   gets a later Python beside it.
2. **Gravitee Lab**: it clones the lab in `~/gravitee-lab`. A clone already there is kept as it is, and a folder of that
   name that holds something else stops it.
3. **Settings**: it makes `.env` and the demo certificates (`make init`).
4. **Command**: it links the command `gravitee-lab` into `~/.local/bin`. When that folder is not on your `PATH`, it adds one
   line, marked `# Added by Gravitee Lab`, to the profile of your shell (`~/.zshrc`, `~/.bashrc`, `~/.bash_profile` on
   macOS, fish's `conf.d`, or `~/.profile`): open a new terminal to use the command.
5. **Docker**: it only looks. The Lab UI's first page says how to install or start it.

Run it again at any time: it skips what is done and never deletes anything. Next time, run `gravitee-lab`. Keep the terminal
open while you use the Lab UI: Ctrl+C stops the Lab UI, and the containers keep running.

| Variable | What it does |
|---|---|
| `GRAVITEE_LAB_DIR` | The folder of the clone, instead of `~/gravitee-lab` |
| `GRAVITEE_LAB_REPOSITORY`, `GRAVITEE_LAB_BRANCH` | What to clone: a fork, a branch |
| `GRAVITEE_LAB_YES=1` | Answers yes to its questions, for a machine with no terminal to ask in |
| `GRAVITEE_LAB_NO_START=1` | Stops before the Lab UI |
| `NO_COLOR=1` | Prints without colors |

Put a variable before `sh`: `curl -fsSL https://raw.githubusercontent.com/gravitee-io-labs/gravitee-lab/main/install.sh | GRAVITEE_LAB_DIR=~/labs/gravitee sh`.

On Windows, see [Windows with WSL 2](#windows-with-wsl-2). To install by hand, see [Prerequisites](#prerequisites), then
[Run it](#run-it).

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

**Tested on** macOS (Apple silicon), and Windows 11 with WSL 2 and Docker Desktop from `install.ps1`, by the maintainer. CI runs the checks and the tests on Ubuntu. Linux is expected to work.

### macOS

Run `xcode-select --install`: Apple's command line tools bring Git, Make and Python. macOS also offers them at the first `git`:
click **Install**, then run the command again. Then install [Docker Desktop](https://docs.docker.com/desktop/setup/install/mac-install/)
(`brew install --cask docker-desktop` with Homebrew) and open it once. The Lab UI's first step links to it too. macOS ships openssl.

When the window of the command line tools says the software cannot be installed, free a few GB of disk and run the command
again. When the tools are installed and `git` or `make` still fails, run `sudo xcode-select --reset`.

### Linux

Install Docker Engine and the Compose plugin from [Docker's repository](https://docs.docker.com/engine/install/), because distribution packages are often older than Compose 5.0. Then run `sudo apt install git make openssl python3` (Debian, Ubuntu) or `sudo dnf install git make openssl python3` (Fedora). To run Docker without `sudo`, run `sudo usermod -aG docker $USER` and log in again.

On a system whose `python3` is older than 3.9 (Ubuntu 20.04, Rocky Linux 8, openSUSE Leap), install a later Python beside it, such as `python3.9`, `python3.12` or `python312`: the lab finds it by its name.

### Windows with WSL 2

The lab runs in WSL 2, in a Linux distribution, with Docker Desktop. In PowerShell, run:

```powershell
irm https://raw.githubusercontent.com/gravitee-io-labs/gravitee-lab/main/install.ps1 | iex
```

It uses what your computer has, adds only what is missing, then opens the Lab UI in your browser:

1. It installs WSL if it is not there, and asks you to restart Windows and to run the command again. Windows asks for
   administrator rights.
2. It uses your Ubuntu or Debian distribution, the default one first, and installs Ubuntu when you have none. A new
   Ubuntu asks for a user name and a password for Linux. They are not your Windows ones: `sudo` asks for this password.
   It asks before it moves a distribution of WSL 1 to WSL 2.
3. It installs the tools it lacks among Git, Make, Python and OpenSSL.
4. It uses the Docker that answers in the distribution, Docker Desktop or Docker Engine. Without one, it offers to
   install Docker Desktop with `winget`: at work, Docker Desktop can need your company's subscription, and your IT may
   install it for you. It starts Docker Desktop and connects it to the distribution. It asks before it restarts Docker
   Desktop while containers run, and makes the distribution the default of WSL only when the default was Docker
   Desktop's own.
5. It clones the lab in the distribution's own disk, in `~/gravitee-lab`: the containers read a folder of Windows
   slowly. A clone already there is kept, and a folder of that name that holds something else stops it.
6. It adds **Gravitee Lab** to the Start menu, which opens the Lab UI next time, then starts the Lab UI.

Run it again at any time: it skips what is done and never deletes anything. Keep its window open while you use the lab:
Ctrl+C stops the Lab UI, and the containers keep running. `GRAVITEE_LAB_DISTRO` names the distribution to use. The
commands of this page run in the distribution: open it from the Start menu, then `cd ~/gravitee-lab`. PowerShell, cmd
and Git Bash cannot run the lab. Items that run AI models through Ollama on Windows are untested.

To read the script before it runs, download it, open it, then run it:

```powershell
irm https://raw.githubusercontent.com/gravitee-io-labs/gravitee-lab/main/install.ps1 -OutFile install.ps1
```

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1
```

When something stops it (a VPN, a proxy of your company, a port taken on Windows), it says why and links to
[Windows and WSL 2](troubleshooting.md#windows-and-wsl-2).

To do it yourself: run `wsl --install`, install [Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/)
and turn on your distribution in its Settings > Resources > WSL integration, install the tools in the distribution as on
Linux, then clone in `~/gravitee-lab` and run `make ui` there. [Windows and WSL 2](troubleshooting.md#windows-and-wsl-2)
has the details.

## Run it

```bash
gravitee-lab
```

Without the installer, clone the lab and run `make ui` in it:

```bash
git clone https://github.com/gravitee-io-labs/gravitee-lab.git
```

```bash
cd gravitee-lab
```

```bash
make ui
```

`gravitee-lab` and `make ui` do the same. `make ui` creates what a fresh clone lacks (`.env`, the demo certificates, the plugin folders and `.lab/on.yml`), starts the Lab UI and opens http://localhost:9000, on its Dashboard. Its first visit checks your machine (Docker, its memory, the disk, the ports, Git) and gives the fix of anything missing: [Your machine](lab-ui.md#your-machine).

In the Lab UI, click **Start all**. The first start pulls the images, about 15 GB: allow about 10 to 15 minutes, depending on your connection. Then open a console from the **Dashboard**. Sign in to the API Management and Gamma consoles with `admin` / `admin`, and to the Access Management console with `admin` / `adminadmin`. Gamma is Gravitee's console for AI agents, MCP servers and LLM traffic, and part of API Management. The other demo logins are in [Logins](configuration.md#logins).

To go further, turn on add-ons, setups and a playground from their pages. An add-on is one extra capability, a setup turns several add-ons on together, and a playground is a use case with its apps, sample data and a guide. [The Lab UI](lab-ui.md) says what each button does.

### Without the Lab UI

The Lab UI runs `docker compose` for you. To do it yourself, use the Makefile (`make help` lists every target). From any
folder, `gravitee-lab` runs the same targets: `gravitee-lab stop`, `gravitee-lab logs SVC=apim-gateway`, `gravitee-lab help`.

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
- **Stop the containers**: run `gravitee-lab stop` (`make stop` in the clone), or click **Stop** on the Containers page. The
  data stays.
- **Update**: click **Update to** in the menu when a newer version is out ([Versions of the Lab](lab-ui.md#versions-of-the-lab)). From a terminal, or for a clone with changes of its own: stop the platform with `make stop`, run `make update` (a fast-forward `git pull`, then the download of the catalogs and the move of your settings if the layout changed: see [Updating a Lab from before catalogs](catalogs.md#updating-a-lab-from-before-catalogs)), then `gravitee-lab`. **Update** on the Containers page pulls newer images of the versions you chose.
- **Remove**: run `gravitee-lab uninstall` to take the command off your `PATH` (the link and the line of your profile that
  the installer added). Then run `make reset` in the clone to delete the containers, the data volumes and what the seeds recorded (a seed is a one-time job that creates a playground's sample APIs and users; `make reset` asks first), then delete the folder. To start clean and keep the folder, run `make reset` and start again, or use **Reset** in the Containers page's menu, which also turns off the playground that is on. On Linux, containers can leave files that your user cannot delete (in `plugins/` and in the `.state/` folders of the installed playgrounds): if `rm` says "Permission denied", use `sudo rm -rf`. To free the disk, `docker image prune -a` removes every image that no container uses, also those of other projects. On Windows, run `make reset` in the distribution, delete `~/gravitee-lab` there and **Gravitee Lab** from the Start menu; `install.ps1` leaves WSL, the distribution and Docker Desktop, which you may use for other things: remove them from Windows' Settings > Apps if you do not.

## Help

- Start with [Troubleshooting](troubleshooting.md).
- For a bug or a question about the lab, open an issue in the Issues tab of this repository. Include your operating system, `docker --version`, `docker compose version`, `git rev-parse --short HEAD`, the output of `make ps` and the log lines that matter. Remove your license, tokens and passwords first.
- For a vulnerability, follow [SECURITY.md](../SECURITY.md).
- For questions about Gravitee products, see the [documentation](https://documentation.gravitee.io) and the [community](https://community.gravitee.io).
