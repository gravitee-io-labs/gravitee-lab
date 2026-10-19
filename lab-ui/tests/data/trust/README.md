# Goldens of the trust policy

One JSON file per unit of `tests/data/catalogs/hostile/`, and `core.json` for the core (its whole resolved configuration, the
names of the settings it reads and the one that holds its license), written by
`python3 lab-ui/tests/data/regen.py` with the Docker CLI and Compose 5 (no daemon). The tests judge these files and never
run Compose. A unit file holds:

| Key | What |
|---|---|
| `unit` | what `trust.judge` reads of the unit: `kind`, `id`, `name`, `meta` (its `x-gravitee` block), `references` and `seed_references` (the settings it reads), `includes` (the targets of its own include list) |
| `config` | the unit with the core as `docker compose config --format json` resolves it, cut to what the policy reads: the unit's own and overlaid services and the top-level data it declares; `null` when Compose could not read it |
| `own`, `overlaid` | the services it defines, and what it adds to a service of the core or of a unit it includes |
| `error` | the last line Compose printed when it could not read the unit |
| `files` | the text of its compose file, so a test can run the text screen on it |
| `never_compose` | the text screen refuses the unit: Compose is never asked about it |

Compose runs with a path, a fake home and Docker's own configuration folder, and nothing else of the maintainer's shell: a license key or a token that a unit reads would otherwise be interpolated into a golden and committed.
The folder of the capture is written `@ROOT@`; `regen.case` puts a real temporary folder in its place. `${HOME}` in the
capture is the fake `/home/hostile`. A golden changes when a unit changes (a test fails until it is captured again) or when
Compose does: read the diff of a capture before you commit it.

`support/partner/` stands for the catalog the hostile units include from; it is copied into the capture and never judged.

To add the unit of a rule: write it under `catalogs/hostile/<kind>s/<code>-<what>/`, run `regen.py`, run `regen.py --expected`,
read the new lines of `expected-hostile.json` against the trust policy, and add the test to `test_trust.py`.
