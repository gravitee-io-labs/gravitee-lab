# Seeder

The seeder is the engine every playground runs to create its data in Gravitee: OpenFGA stores, an Access Management
domain, APIs, applications, subscriptions, groups and developer portals in API Management (in its own environment and in
those a playground adds), the objects of Gamma, and the Kubernetes resources that the Gravitee Kubernetes Operator turns into API Management objects. It talks to the platform only
through its management APIs, and records what it creates so that it can delete exactly that later.

## How a playground runs it

A playground has one service that builds `../../seeder`, mounts its `seed/` folder at `/seed` and its `.state/` folder
at `/state`, and carries the label `io.gravitee.role: seed`. The Lab UI runs it when a Start sets the playground up and
on *Re-seed*, and runs `seed.py --remove` for *Delete what it created*. The service and the rules for seed files are in
[the seed](../docs/playgrounds.md#the-seed). The image is `ghcr.io/gravitee-io-labs/gravitee-lab/seeder`, built from
[`Dockerfile`](Dockerfile) with Python 3.15, `requirements.txt` and the OpenFGA command-line tool.

## Layout of this folder

| Where | What |
|---|---|
| `lab_seeder/` | The seeder: one module for each thing it seeds, the helpers they share (`common.py`, `controls.py`, `refills.py`, `gamma.py`), the lint of seed files (`seedlint.py`) and the kit of the tests (`testkit.py`) |
| `seedlint.py` | The path a catalog runs the lint at (`python lab/seeder/seedlint.py`): it hands over to the module of the package |
| `tests/` | The tests, and the invented samples they read (`fixtures/samples/`) |
| `Dockerfile`, `requirements.txt` | The image |
| `README.md`, `AGENTS.md` | This page, and the guide of an agent that works here |

## Layout of a seed folder

A module reads its own folder and does nothing when it is absent. They run in this order, and `--remove` runs them in
reverse.

| Module | Reads |
|---|---|
| `openfga.py` | `seed/openfga/*.yaml` |
| `am.py` | `seed/am/domain.yaml` (settings, scopes, users, groups), `applications/`, `protected-resources/` |
| `apim.py` | `seed/apim/settings.yaml`, `apis/`, `applications/`, `subscriptions/`; and, for each `seed/environments/<id>/` (an environment of its own, which it looks up and never creates), `settings.yaml`, `users.yaml`, `groups/`, `apis/`, `applications/`, `subscriptions/` |
| `apim_portal.py` | `portal/` of `seed/apim/` and of each `seed/environments/<id>/`: `theme.yaml`, `portal.yaml`, `pages/`, `content/`, `links/`, `listings/`, the optional `api-overview.md` (the text of the Overview page of every listed API), `api-details.md` (the Details tab of the page of each) and `api-docs/<feed>.yaml` (the AsyncAPI or OpenAPI document of the listed API `<environment id>-<feed>`), and the `assets/` the theme names |
| `am_link.py` | `seed/am/gamma-link.yaml` |
| `aim_catalog.py` | `seed/aim/credentials/`, `providers/`, `models/`, `mcp-servers/`, `knowledge/`, `skills/`, `agents/`, `api-tools/` |
| `aim_workspaces.py` | `seed/aim/workspaces/` |
| `aim_proxies.py` | `seed/aim/mcp-proxies/`, `studios/` (with the `subscriptions` of applications of `seed/apim/` to a studio's plan) |
| `aim_agents.py` | `seed/aim/agents/` (the keys `identity` and `access`, which need the amp-demo build: API Management 4.13.1 serves no application for an agent, and the seed stops with a line that says so; `aim_catalog.py` reads the card and the metadata) |
| `aim_governance.py` | `seed/aim/governance/` (`approvals.yaml`, `compliance.yaml`) |
| `authz.py` | `seed/authz/entities.yaml`, `policies/`, `schema.yaml`, `schemas/` |
| `aim_dashboards.py` | `seed/aim/dashboards/` |
| `k8s.py` | `seed/k8s/*.yaml`: Kubernetes manifests, applied to the cluster of `KUBECONFIG` for the Gravitee Kubernetes Operator to act on |

`controls.py` and `refills.py` read `seed/controls.yaml` and `seed/refills.yaml`. `common.py` holds the shared helpers
and `gamma.py` the client of the Gamma modules.

A file is named after the object it describes, as its id or its slug: `acme-bakery-llm.json` for the API `Acme Bakery LLM`,
`acme-bakery-ace.yaml` for the agent with the entity id `acme-bakery-ace`. Files are read in name order, so prefix them with
`01-`, `02-` to choose the order, as Acme Bakery's policies do.

## Order and parallelism

The modules run one after the other, in the order of the table above, and a removal in the reverse. What a module knows to
be independent, it makes at the same time:

- **Inside a module.** The groups, the APIs and the applications of an environment, then its subscriptions; the environments;
  the portals; the agent cards and the MCP servers the catalog reads; what a removal deletes of one kind. A folder whose
  files depend on one another (the pages of a portal, the manifests of a cluster, the policies of a schema) stays in name
  order. The platform's users are the organization's, and it does not make them well at the same time (a user was not found
  after it was made, or a search missed one): one environment at a time makes them, behind a lock, and looks for a new user
  until the platform lists it.
- **Waits.** The seed does not wait for the gateway: what the management API holds is what the seed made, and the gateway takes
  it within seconds. `SEED_WAIT_GATEWAY=1` brings the waits back (every API served, the protections and the budgets in force, a
  chat through every LLM proxy). What the seed must wait for to be right stays: an API that is replaced leaves the gateway
  before it is imported again, a user that a group has just made is looked for until the platform lists it, and with `AM_GATEWAY_URL` the AM gateway
  serves the domain and knows each application that has a secret, which a service that starts after the seed signs in with
  at once.
- **Load.** `SEED_WORKERS` (4 by default) is the number of requests in flight at any moment, whatever the number of steps that
  wait for an answer; `1` runs everything in order. The first step that fails stops the others at their next request, and
  its failure is the last line of the output.

A step that is new goes through `common.parallel` or `common.together` for what is independent in it; a request goes through
`Api`, a wait through `pause`, and a record through `common.record`, which is safe to call from several steps.

## Settings

The seed service's environment is the seeder's settings: the endpoints, the credentials and the timeouts. They are
listed, with their defaults, in [the seed's settings](../docs/playgrounds.md#settings).

## Work on the seeder

The module contract, what the Lab UI reads from a run, the behavior to keep and how to run the tests are in
[AGENTS.md](AGENTS.md).
