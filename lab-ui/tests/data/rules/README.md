# Fixtures of the rule tests

What the rule tests of the catalog standard read (`tests/catalogs/rules/`, `tests/catalogs/ci/`, `tests/catalogs/test_tree.py`, `tests/catalogs/test_report.py`).

| Path | What it is |
|---|---|
| `support.py` | The good catalog, the helpers that change it, the secrets a test plants (made of pieces when the test runs, so no file here holds one), and the case that makes work trees with git |
| `dockerfiles/` | A Dockerfile that passes |
| `guides/` | A guide with every Markdown form the rules read, and an SVG file with no active content |
| `compose/` | Compose files of units: builds, images, the seeder |

The files are data. Nothing here is run.
