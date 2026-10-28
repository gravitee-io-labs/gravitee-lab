# Troubleshooting

Check your setup first, then find your symptom. Each entry gives the symptom, the cause and the fix.

## Check your setup

Docker Compose must be 5.0 or later:

```bash
docker compose version
```

Docker needs about 8 GB of memory, more for items that run AI models, and 12 GB for several add-ons. This prints what
it has, in bytes:

```bash
docker info --format '{{.MemTotal}}'
```

The ports of the lab must be free. [Ports](configuration.md#ports) lists them. To see what holds one, such as 8083:

```bash
lsof -nP -iTCP:8083 -sTCP:LISTEN
```

On Linux, use `ss -ltnp 'sport = :8083'` instead. On Windows, run `netstat -ano | findstr :8083` in PowerShell. A
container holds a port through Docker, so this names the container:

```bash
docker ps --filter publish=8083
```

## Problems and fixes

### Docker is not reachable

**Symptom:** the Lab UI banner says `Docker is not reachable`, with Docker's own words after it, and the health chip is
red. In a terminal, `make` says `Docker is not running: start Docker Desktop (or the docker service), then try again`.

**Cause:** the Docker daemon is not running. If Docker's own words are `permission denied`, see
[Permission denied on /var/run/docker.sock](#permission-denied-on-varrundockersock).

**Fix:** start Docker Desktop, or on Linux run `sudo systemctl start docker`. The Lab UI recovers by itself. What was
stopped from the Containers page stays stopped.

### Docker or Docker Compose is missing or too old

**Symptom:** `make all`, every other target that uses Docker, and `python3 lab-ui/server.py --check` stop with one line.
`make ui` opens the Lab UI all the same, and its first step, *Your machine*, says what to install:

- `Docker Compose 5.0 or later is needed, found: 2.40.3 (see Prerequisites in README.md)`, with `found: none` when the
  Compose plugin is missing.
- `docker not found: install Docker with Compose 5.0 or later (see Prerequisites in README.md)` when there is no
  `docker` program at all.

Or Compose says an add-on `conflicts with imported resource`.

**Cause:** an add-on or a playground changes a core service from an included file. Compose accepts that from 5.0.

**Fix:** update Docker Desktop. On Linux, install `docker-compose-plugin` from
[Docker's repository](https://docs.docker.com/engine/install/): the packages of Linux distributions are often older.
Then check `docker compose version`.

### macOS asks for its command line developer tools

**Symptom:** the first `git` or `make` says `xcode-select: note: No developer tools were found, requesting install`, and a
window offers to install them. Or that window ends with `Can't install the software`.

**Cause:** a Mac has Git, Make and Python only with Apple's command line tools.

**Fix:** click **Install**, then run the command again. When the install fails, the disk is short of space (it needs a few
GB, more when macOS has an update waiting) or Apple's servers did not answer: free some space, then run
`xcode-select --install` again.

### Python or OpenSSL is missing, or Python is too old

**Symptom:** a `make` target stops with one line:

- `python3 not found: install Python 3.9 or later (see Prerequisites in README.md)`
- `Python 3.9 or later is needed, found: Python 3.8.10 (see Prerequisites in README.md)`
- `openssl not found: install it (see Prerequisites in README.md)`

`make setup` has two more. `The seeder needs Python 3.10 or later, found: Python 3.9.6` is followed by `Run make setup
again with a newer python3 (for example PYTHON3=python3.12 make setup)`. And `Cannot make .venv: install Python 3.10 or
later with venv (Debian, Ubuntu: apt install python3-venv)`.

**Cause:** the Lab UI runs on Python 3.9 or later. The seeder's tests and `make lint` need Python 3.10 or later, in a
`.venv` that `make setup` makes. `make init` and `make certs` need `openssl`.

**Fix:** install the missing tool, then run the target again. To pick the Python for `.venv`, name it:
`PYTHON3=python3.12 make setup`. `make setup` removes the `.venv` it made when that Python is too old. When a `.venv`
was there before, the message says `.venv has Python 3.9.6` and starts with `Remove .venv`: remove it yourself, then run
`make setup` again.

### Permission denied on /var/run/docker.sock

**Symptom:** a message that starts with `permission denied while trying to connect to the` and ends with
`unix:///var/run/docker.sock`, in the banner or in a terminal. In a terminal, `make` prints that line, then
`Docker is running but your user cannot reach it: add yourself to the docker group (sudo usermod -aG docker $USER), then
log in again (see Troubleshooting in docs/troubleshooting.md)`.

**Cause:** on Linux, Docker runs and your user is not in the `docker` group.

**Fix:** add yourself, then log out and back in:

```bash
sudo usermod -aG docker $USER
```

### A port is already allocated

**Symptom:** a start fails with `port is already allocated`, `Bind for 127.0.0.1:8083 failed`, or `address already in
use`.

**Cause:** another program, or another copy of the lab, holds the port.

**Fix:** find the holder with the commands in [Check your setup](#check-your-setup), stop it, and start again. A local
MongoDB, Redis, Elasticsearch or Kafka often holds one. Free the port rather than move the lab's: the consoles call
each other at these addresses. [Ports](configuration.md#ports) lists every port.

### Port 9000 is taken, or the Lab UI is already running

**Symptom:** `make ui` prints one of:

- `Port 9000 is used by another program. Free it, or use another port: UI_PORT=9001 make ui (the UIs of a playground do
  not show next to the guide on another port)`
- `Gravitee Lab UI is already running on http://localhost:9000. To load a new version: stop it with Ctrl+C in its
  terminal, then run make ui again`
- `UI_PORT must be a port number, for example 9001`

**Cause:** another program listens on 9000, you started the Lab UI twice, or `UI_PORT` holds something else than a port
number.

**Fix:** for the second message, use the Lab UI that runs. It acts on the clone that started it: if you meant another
clone, see [A second clone collides with the first](#a-second-clone-collides-with-the-first). For the first, free port
9000 if you can. With `UI_PORT=9001 make ui` the Lab UI works, but the UIs of a playground show as refused beside the
guide, because the platform's UIs and the apps let only port 9000 frame them. For the third, set `UI_PORT` to a number
from 1 to 65535, or leave it empty.

### The Lab UI says to open it at localhost

**Symptom:** you open the Lab UI by another name, such as `0.0.0.0` or the machine's name, and the page says `Open the
Lab UI at http://localhost:9000`.

**Cause:** the Lab UI answers only to the names `localhost` and `127.0.0.1`, so that no other site can reach it.

**Fix:** open the address the page gives. To reach it from another machine, see
[Headless or remote machine](#headless-or-remote-machine).

### A UI says it cannot be shown here

**Symptom:** a tab of a playground says `cannot be shown here`.

**Cause:** the Lab UI is not on port 9000, or the app's own headers refuse frames.

**Fix:** open http://localhost:9000, or use *Open in a new tab*.

### Docker Hub says toomanyrequests

**Symptom:** a pull fails with `toomanyrequests`.

**Cause:** Docker Hub limits the pulls of anonymous users.

**Fix:** log in with a Docker account, which has a higher limit, then start again:

```bash
docker login
```

### Out of memory, exit 137, or restarting

**Symptom:** a container exits with code 137 or keeps restarting. The Lab UI says `Out of memory` or `Keeps
restarting`, and the banner may say `Docker memory: ... 8 GB+ recommended`.

**Cause:** Docker is short of memory for what runs.

**Fix:** give Docker more memory, or turn add-ons off. In Docker Desktop, change *Settings*, *Resources*, *Memory*. On
Linux, Docker uses the memory of the machine. On Windows with WSL 2, see [Windows and WSL 2](#windows-and-wsl-2).

### The first start is slow

**Symptom:** the first Start takes many minutes.

**Cause:** the first start pulls several gigabytes of images and builds the apps of a playground. An item that
runs AI models through the gateway can also download them, the first time it deploys the LLM proxy. The Gravitee
components start in order: the databases, then the Management API, then the gateways and the UIs, which wait until
their API is healthy.

**Fix:** wait. Follow the phases on the page, the Activity drawer, or the Logs page. `make pull` fetches the images
ahead of time. If the seed of a playground ends with a timeout while waiting for the gateway, raise its
`<ID>_GATEWAY_READY_TIMEOUT` setting, in seconds, when the playground has one.

### Start is disabled

**Symptom:** the Start button of a playground is grey, and its row says *Needs newer versions* or *Needs settings*. The
reason reads `Needs Access Management 4.13.0-alpha.9 or later and API Management 4.13.0-alpha.5 or later: choose them on the Images
page`, or `<name> needs settings`; the badge leads to the Images page or to that add-on.

**Cause:** the playground needs later or pre-release builds of API Management or Access Management, and the core's
default versions are older. Or an add-on that the playground brings lacks a setting that has no default.

**Fix:** for the images, open the Images page and choose a 4.13 pre-release (alpha) tag: `4.13.0-alpha.5` or later for
API Management, `4.13.0-alpha.9` or later for Access Management. One visit is enough: the reason names every image that is too old. For a
setting, open the key button of the add-on and fill in the field marked *Required*.

### A start fails right after a version change

**Symptom:** a component fails to start after you chose another version on the Images page.

**Cause:** a version switch on existing data can fail on migrations.

**Fix:** `make reset` starts clean. It deletes all data.

### A seed stops on an API it did not create

**Symptom:** a playground's seed ends with `API <name>: /<path>/ is the path of '<name>', which the seed did not create`, though
you never made that API.

**Cause:** the platform made the API and the seed lost the record of it, so it takes the API for someone else's. The seed
records an API whose import fails with an error of the platform, but not one whose request got no answer (a timeout): the
platform may finish the import after the seed has stopped waiting. A lost `.state/created.json` does the same.

**Fix:** delete the API named in the message in the Console (stop it, then delete it), then choose *Re-seed*. For an
environment of its own, open that environment first.

### Empty folders named server.p12 and ca.pem in certs/mtls

**Symptom:** `certs/mtls/server.p12` and `certs/mtls/ca.pem` are empty folders, not files.

**Cause:** the gateway mounts these two files one by one, and Docker makes an empty folder in place of a mounted file
that is missing. A `docker compose up` typed before `make init` does that.

**Fix:** run `make init`. It removes the two folders and makes the certificates, then start again. An add-on that mounts a certificate it makes in
its own folder has the same trap: remove the empty folder it mounts in place of the file and start the add-on again.

### A license change does not take effect

**Symptom:** you saved a license, and the features it gives are still off.

**Cause:** the containers read the license when they are created. The heads of Containers and the Dashboard, the banner
and the rows show the badge `Apply needed`; hover it to read which containers run with settings changed since they started (`GRAVITEE_LICENSE`).

**Fix:** choose *Apply* in the banner's notice or on the Containers page. The badge then goes.

### Alert Engine keeps restarting

**Symptom:** without a license, the Alert Engine's row on the Containers page says `Needs a license`, and the Lab UI
does not start it. If you start it by hand, its row is red and says `Keeps restarting`, and `make ps` shows
`gio-alert-engine` restarting. The rest of the platform runs.

**Cause:** the Alert Engine needs an Enterprise license. Without one it stops with `Alert Engine cannot start because
the license is missing or has insufficient permission`, and Docker starts it again.

**Fix:** add a license from the *Community Edition* button on the core's line of the Containers page, then click *Start* again:
[License](configuration.md#license) has the steps, and *Apply* recreates the containers. To get a license, see
[What needs a license](installation.md#what-needs-a-license). Without a license, API Management, Access Management and
Gamma's community features run.

### A plugin does not load, or `.work` fails

**Symptom:** a product logs `DirectoryNotEmptyException ... .work`, or a job of the Plugins page fails with
`apim/.work could not be cleared`, often with `Permission denied` on Linux.

**Cause:** `.work` is the cache a product unpacks plugins into, in `plugins/apim/` or `plugins/am/`. A stale one breaks
loading. On Linux, the containers write it as user 1001, so your user cannot delete it.

**Fix:** remove it, then restart:

```bash
rm -rf plugins/apim/.work
```

```bash
make restart
```

Use `plugins/am/.work` for Access Management. On Linux the files may belong to another user: add `sudo` to the first
command.

If an added plugin does not load, check the Plugins page. A plugin found twice, bundled in the image and added in the
folder, loads from the most recent file, not the highest version. The page says `Added, ignored` when the bundled one is
newer.

### Gamma's Tracing pages list nothing

**Symptom:** Agent Management, Observability, Tracing shows no call.

**Cause:** tracing must be enabled on the API (a playground's LLM, MCP and A2A proxies can have it), and the Gamma Spans
add-on must be on and running. A page lists the last 5 minutes at first.

**Fix:** turn Gamma Spans on, pick a longer range, and wait a few seconds after the call. Jade and the AI Agent
Inspector cannot run with Gamma Spans: all three take the gateway's OpenTelemetry exporter.

### Ollama is not reachable from the containers

**Symptom:** the checks of an item that uses Ollama say `Not reachable` or `Reachable, but qwen3:0.6b is missing`.

**Cause:** Ollama is not running, a model is missing, or the containers cannot reach it. On Linux and WSL 2, Ollama
listens on `127.0.0.1` only by default, which they cannot reach. The containers use `host.docker.internal`, which the
compose files map to the gateway address of Docker's bridge network.

**Fix:** pull the models the item names; the examples below are the ones its README lists.

```bash
ollama pull qwen3:1.7b
```

```bash
ollama pull qwen3:0.6b
```

On Linux and WSL 2, make Ollama listen where the containers reach it:

1. First choice: bind Ollama to the Docker bridge address. Find it, then start Ollama with `OLLAMA_HOST` set to it:

   ```bash
   docker network inspect bridge --format '{{(index .IPAM.Config 0).Gateway}}'
   ```

   With systemd, run `sudo systemctl edit ollama`, add `Environment="OLLAMA_HOST=<that address>"` under `[Service]`,
   then `sudo systemctl restart ollama`. Ollama then listens on the Docker bridge of this machine only, not on your
   network. It no longer answers on `localhost`, so the Lab UI's check says `Not reachable` though the containers reach
   it, and your own `ollama` commands need the same `OLLAMA_HOST`.
2. If that does not work, listen on every interface with `OLLAMA_HOST=0.0.0.0`, set the same way. That opens Ollama to
   your network too, with no sign-in: use a firewall.

If Ollama runs elsewhere, set `OLLAMA_URL` in the settings of the playground. On Windows, see
[Windows and WSL 2](#windows-and-wsl-2).

### A second clone collides with the first

**Symptom:** a second copy of the lab replaces the containers of the first, or fails on ports.

**Cause:** one lab runs per machine. Every clone uses the Compose project `gravitee`, the container names `gio-*`, the
volumes `gravitee_*` and the same host ports.

**Fix:** use one clone at a time. Run `make stop` in the first before you start the second. They share the data
volumes, but each clone keeps its own record of what its seeds created (`.lab/catalogs/<catalog>/playgrounds/<id>/.state/`). To start the second
clean, run `make reset` in it: it deletes the shared volumes and empties that clone's records. The first clone's records
then no longer match the volumes, so run `make reset` there too before you use it again.

### Windows and WSL 2

Windows is supported through WSL 2 only. Native PowerShell, `cmd` and Git Bash are not supported. The maintainer tests on
Windows 11 with WSL 2, Ubuntu and Docker Desktop, so tell us what you find elsewhere. Items that run AI models through
Ollama on Windows are untested.

- **Run everything in WSL.** `install.ps1` sets it up ([Windows with WSL 2](installation.md#windows-with-wsl-2)). To
  do it yourself, open an Ubuntu distribution, install the tools with `sudo apt install git make openssl python3`, then
  clone and run `make ui` there.
- **Docker.** Use Docker Desktop with WSL integration turned on for your distribution, or Docker Engine inside it.
  Check `docker compose version` inside WSL. The Lab UI's first step starts Docker Desktop when it is stopped.
- **Docker Desktop lost Ubuntu.** When the distribution restarts (`wsl --shutdown`, `wsl --terminate`), Docker Desktop
  stops serving it until Docker Desktop restarts, and may say "Restart the WSL integration". Click **Reconnect** in the
  Lab UI's first step, or run `docker desktop restart` in PowerShell.
- **The `docker` of Windows in WSL.** Without the integration, `docker` in the distribution is Docker Desktop's command
  for Windows, which only says to turn the integration on: turn it on in Docker Desktop's Settings > Resources > WSL
  integration.
- **Clone in the Linux filesystem**, for example in `~/gravitee-lab`, not under `/mnt/c`. Access under `/mnt/c` is
  slow, and it does not reliably keep the Unix permissions that the certificates and `.env` rely on.
- **Memory.** Docker's memory comes from WSL. Create `%UserProfile%\.wslconfig` with the lines below, then run `wsl
  --shutdown` in PowerShell and start Docker again:

  ```ini
  [wsl2]
  memory=12GB
  ```

- **Browser.** `make ui` opens http://localhost:9000 in your Windows browser. If it opens none, open it yourself.
- **A VPN.** A VPN often keeps WSL from resolving the names of sites while Windows resolves them: `apt`, `git clone` and
  the catalogs fail, and `install.ps1` says so. Add the lines below to `%UserProfile%\.wslconfig`, run `wsl --shutdown`
  in PowerShell, then start Docker Desktop and the command again. If it still fails, add `networkingMode=mirrored` under
  the same heading.

  ```ini
  [wsl2]
  dnsTunneling=true
  ```

- **A port held by a program of Windows.** Docker Desktop publishes the platform's ports on Windows, so a database or a
  server installed on Windows that listens on one of them keeps the platform from starting. The Lab UI's first step names
  the port. Port 9000, the Lab UI's, held on Windows sends the browser to that program: `install.ps1` names it.
- **Hosts file.** A client of an add-on that asks for host names, such as a Kafka client, that runs on Windows reads
  `C:\Windows\System32\drivers\etc\hosts`. Edit it as an administrator. The add-on's README has the names.

### Headless or remote machine

The Lab UI listens on `127.0.0.1` and answers only to the names `localhost` and `127.0.0.1`, so you cannot reach it by
the machine's name. Start it without a browser, then forward the ports to your own computer.

On the remote machine, create what a fresh clone lacks, then start the Lab UI:

```bash
make init
```

```bash
python3 lab-ui/server.py --no-open
```

On your computer, forward port 9000 and the ports of the consoles you use. This one covers the core:

```bash
ssh -N -L 9000:127.0.0.1:9000 -L 8081:127.0.0.1:8081 -L 8082:127.0.0.1:8082 -L 8083:127.0.0.1:8083 \
  -L 8084:127.0.0.1:8084 -L 8085:127.0.0.1:8085 -L 8086:127.0.0.1:8086 -L 8092:127.0.0.1:8092 \
  -L 8093:127.0.0.1:8093 user@remote-machine
```

Open http://localhost:9000. The console of an add-on or a playground needs its own `-L`: [Ports](configuration.md#ports) has the numbers.

### Corporate proxy or offline use

Docker pulls the images, so Docker needs the proxy: set it in Docker Desktop, or in the daemon's configuration. The Lab
UI reads `HTTP_PROXY` and `HTTPS_PROXY` from the shell that started it, for the plugin catalog, the image tags and the
checks of other hosts, such as huggingface.co. Addresses on this machine (`localhost`, `127.0.0.1`, `::1`), such as the
check for Ollama, never go through the proxy.

A proxy that inspects HTTPS signs each site with your company's certificate. Windows trusts it, a Linux of WSL does not, so
`git clone` and the catalogs fail with a certificate error. `install.ps1` finds the certificate Windows trusts for github.com
and, once you say yes, adds it to the distribution. To do it yourself, ask your IT for that certificate (a `.crt` file in
PEM), then in the distribution run `sudo cp company.crt /usr/local/share/ca-certificates/ && sudo update-ca-certificates`.

Without internet, once the images are pulled and the apps are built, the platform runs. These degrade:

- the Images page cannot list tags, the Dashboard shows no update for the Gravitee versions, and the Plugins
  page cannot load its catalog or install a plugin;
- a guide shows as plain text and the file editor as a plain text area, because the browser cannot load their libraries;
- the gateway cannot download the AI models of an item that uses them the first time;
- a new image build cannot download its packages.

[What needs the internet](configuration.md#what-needs-the-internet) lists every case.

## Report a bug

Check this page first, then open an issue at https://github.com/gravitee-io-labs/gravitee-lab/issues. The bug form asks for:

- what you did, what you expected, and what happened;
- your operating system: macOS, Linux or Windows (WSL 2);
- the output of `docker --version` and `docker compose version`;
- the lab revision, from `git rev-parse --short HEAD`;
- the output of `make ps`;
- the lines that matter from `make logs SVC=<service>`, or from the Logs page.

Remove secrets before you paste anything: your license, tokens and passwords. For a security problem, follow
[SECURITY.md](../SECURITY.md) instead of opening an issue. Questions about Gravitee products belong at
https://documentation.gravitee.io and https://community.gravitee.io.
