# Fixtures of the rule tests

What the rule tests of the catalog standard read (`tests/test_rules_*.py`, `tests/catalogs/test_tree.py`, `tests/catalogs/test_report.py`).

| Path | What it is |
|---|---|
| `support.py` | The good catalog, the helpers that change it, the secrets a test plants (made of pieces when the test runs, so no file here holds one), and the case that makes work trees with git |
| `dockerfiles/` | Dockerfiles that pass and Dockerfiles that break one rule each |
| `guides/` | A guide with every Markdown form the rules read, and SVG files with and without active content |
| `compose/` | Compose files of units: builds, images, the seeder |

The files are data. Nothing here is run.
