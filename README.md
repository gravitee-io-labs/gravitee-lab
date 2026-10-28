<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/banner-dark.png">
  <img src="docs/images/banner-light.png" width="100%" alt="Gravitee Lab: ready-made scenarios to play with the Gravitee platform on your laptop">
</picture>

<p align="center">
  <a href="https://github.com/gravitee-io-labs/gravitee-lab/actions/workflows/ci.yml"><img src="https://github.com/gravitee-io-labs/gravitee-lab/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-AGPL--3.0-blue" alt="License: AGPL-3.0"></a>
  <img src="https://img.shields.io/badge/for-local%20demos-orange" alt="For local demos">
</p>

## Run it

```bash
git clone https://github.com/gravitee-io-labs/gravitee-lab.git && cd gravitee-lab && make ui
```

On a Mac that never ran `git`, macOS first offers its command line tools: click **Install**, then run the command again.

On Windows, in PowerShell, this sets up WSL, Ubuntu and Docker Desktop as needed, then does the same ([what it does](docs/installation.md#windows-with-wsl-2)):

```powershell
irm https://raw.githubusercontent.com/gravitee-io-labs/gravitee-lab/main/install.ps1 | iex
```

The [Lab UI](http://localhost:9000) opens in your browser, on its Dashboard. Start the platform, then turn on add-ons, setups or playgrounds from the lists to do even more interesting things. They come from [catalogs](docs/catalogs.md): the Lab downloads the default one the first time it starts.

No license is needed to start. [What needs one](docs/installation.md#what-needs-a-license).

## Prerequisites

Docker with Compose 5.0 or later (8 GB of memory), Git, Make, Python 3.9 or later and OpenSSL, on macOS, Linux or Windows through WSL 2. [Install steps for each system](docs/installation.md#prerequisites).

## What is inside

| | |
|---|---|
| **Core** | API Management, Access Management, Alert Engine, Gamma and their databases |
| **[Catalogs](docs/catalogs.md)** | Where everything else comes from. The default one, Gravitee public, holds the playgrounds (a use case with its apps, sample data and a guide) and the add-ons they build on (a tracing console, an authorization server and more); a catalog can also hold setups (several add-ons started together). Add your own or your team's |

## Learn more

- [Install and run](docs/installation.md)
- [Lab UI](docs/lab-ui.md)
- [Configuration](docs/configuration.md)
- [Gravitee Cloud](docs/cloud.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Catalogs](docs/catalogs.md)
- [Writing a catalog](docs/writing-a-catalog.md)
- [Reading a failed check](docs/catalog-rules.md)
- [The format of add-ons, setups and playgrounds](docs/units.md)
- [Writing a playground](docs/playgrounds.md)
- [Contributing](CONTRIBUTING.md)
- [Security](SECURITY.md)

For local demos and learning, not for production. Licensed under the [AGPL-3.0](LICENSE), with the attribution in [NOTICE](NOTICE).
