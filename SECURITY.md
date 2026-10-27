# Security

Gravitee Lab is a tool for local demos and learning. It is not meant for production, and it is not hardened for a network you do not trust.

## Report a vulnerability

Report a vulnerability in this repository through GitHub's private vulnerability reporting: open the **Security** tab of the repository, click **Report a vulnerability**, and describe the problem. Do not open a public issue for it.

Include the steps to reproduce it, what an attacker gains, and the version you tested (`git rev-parse --short HEAD`). Reports are handled on a best-effort basis.

In scope: the Lab UI, the Makefile and the scripts, the seeder, the CI workflows, and the image published from this repository. A real secret committed to the repository is in scope too. The add-ons, setups and playgrounds, and the apps of the playgrounds, live in catalogs, which have their own security policy: report a problem of an item to its catalog.

Vulnerabilities in Gravitee products or in other software the lab runs are not issues of this repository. Report them to Gravitee through its security process: start from [documentation.gravitee.io](https://documentation.gravitee.io).

## Supported versions

There are no versioned releases. Only the current `main` branch is supported, and fixes land there.

## What the lab exposes

- Every published port binds `127.0.0.1`, so nothing is reachable from other machines. The check of a catalog fails when one of its items publishes a port on another address.
- The Lab UI listens on `127.0.0.1:9000`. It has no sign-in, and it runs Docker commands and edits the lab's files for anyone who can reach it. It refuses requests whose host is not `localhost` or `127.0.0.1` and requests that come from pages of other sites. Do not run it on a machine that other people use, and do not forward or proxy its port.
- Docker access is close to root access on Linux. Anyone who controls the Lab UI controls your Docker.
- Review before you run. Starting a pull request, a personal item or the playground of a catalog that you did not write runs its containers, Dockerfiles, seeds and Makefile code with your Docker access. Read the change first: [Review before you run](CONTRIBUTING.md#review-before-you-run).
- Several consoles and servers have no sign-in at all: the Lab UI, Elasticsearch, MongoDB, and many of the add-ons and playgrounds of the catalogs (a broker's plaintext listener, a console, a mail server, an unprotected demo API or MCP server). Their READMEs say which, and the demo credentials they use are public. All of them bind `127.0.0.1`.
- The folders `plugins/apim` and `plugins/am` are writable by all users (mode 777), because the containers unpack plugins there as another user. The Kafka broker's key, the Kafka gateway's keystore and the gateway's `certs/mtls/server.p12` are readable by all users (mode 644) for the same reason. Treat the machine as single-user.
- Items that use Ollama ask you to run it so that containers can reach it. On Linux and WSL 2, the first choice is to bind Ollama to the address of Docker's bridge, so that only this machine's containers reach it. If that fails, `OLLAMA_HOST=0.0.0.0` makes Ollama listen on every network interface, without any sign-in: use a firewall. [Troubleshooting](docs/troubleshooting.md#ollama-is-not-reachable-from-the-containers) has the steps.
- The core sets `gravitee_modules_aim_catalog_mcp-discovery_allow-private-endpoints` (in `docker-compose.yml`) on purpose, so that Gamma's catalog can discover MCP servers, models and agents at private addresses, such as the apps of a playground. Discovery endpoints can then point at private network addresses, including your machine and your network.
- A hybrid gateway add-on is a second gateway, on port 8090 (`127.0.0.1` only), that serves the APIs of one Gravitee Cloud environment. It sits on the lab's internal network, so an API deployed in that environment can name any container of the lab as its backend, those with no sign-in included. Register only an environment that people you trust can change.
- An account connected on the Gravitee Cloud page leaves tokens on this machine. The Account Token and a Cloud Token for each environment are in the core's `.env`, with the token of each gateway the Lab UI created there, and `HYBRID_GATEWAY_CLOUD_TOKEN`, the token of the gateway that runs here, is in the `.env` of its add-on. The Lab UI does not show them again in its pages or answers. A deploy gives the Cloud Token to the seed by name, in the environment of that one command, never in the command itself. The settings form of the Hybrid gateway add-on shows `HYBRID_GATEWAY_CLOUD_TOKEN` masked, like every secret setting.
- The Gamma and API Management consoles start with a command (`x-ui-frames` in `docker-compose.yml`) that removes `X-Frame-Options` from their nginx configuration and adds `http://localhost:9000` to the `frame-ancestors` of their Content-Security-Policy, so that the Lab UI can show them next to a guide.
- The Developer Portal turns its frame protection on and names `http://localhost:8084` (the APIM Console) and the Lab UI's pages as the pages that may frame it, with its own `ALLOWED_FRAME_ANCESTOR_URLS` setting: before, it sent no frame protection at all, so any page could frame it.
- The Lab UI loads marked, DOMPurify, Mermaid and Monaco from cdnjs.cloudflare.com and cdn.jsdelivr.net, each at a pinned version. A Subresource Integrity hash covers the script the page loads, which for Monaco is its loader: the editor's other files are not hashed. You trust those two CDNs: a script that the page runs can use every Docker action of the Lab UI.

## Demo credentials

The demo logins and secrets are public by design: they are public defaults of the Gravitee images, or the defaults of settings in the Compose files, and [Logins](docs/configuration.md#logins) lists them with their settings. They are not vulnerabilities. Every password, secret, token and key in a Compose file is a setting, never a literal: a demo value is the default of its setting, and the check of a catalog refuses a literal. Change the ones you want, and do not reuse the defaults anywhere else.

The demo certificates are made on your machine by `make init`: the private keys and `client.p12` of `certs/mtls/` are readable by you only, and the certificates an add-on makes are written in its own folder. They are self-signed and valid for ten years. The gateway mounts only `server.p12` and `ca.pem`, so the CA key and the client key never enter a container.

Your Gravitee Enterprise license, and the tokens and keys you enter for the federation and hybrid add-ons and for the Gravitee Cloud page, are yours. An Account Token acts with the rights of the person who made it, and a Cloud Token creates and deletes the APIs of its environment: make the Account Token with a dedicated Cloud user, and revoke both in Gravitee Cloud when you are done. They live in `.env` files, which git ignores. Never paste them in an issue, a pull request or a log.
