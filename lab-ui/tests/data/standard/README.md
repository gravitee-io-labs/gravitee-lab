# Fixtures of the standard tests

What the tests of the catalog standard share (`tests/catalogs/test_standard.py`, `tests/catalogs/test_release_command.py`,
`tests/catalogs/rules/test_codeowners.py`, and `.github/tests/test_catalog_template.py` for the engine's CI).

| Path | What it is |
|---|---|
| `support.py` | The fixed parameters of a catalog (`acme-hub`, the Lab `acme/gravitee-lab`, a made-up commit), a work tree with an origin beside it made in a temporary folder, git run with a person's name set in a temporary home, a copy of the template that a test may change, and the way to run a block of bash taken out of a workflow |

Nothing here is a real name, address or commit. The template itself is `lab-ui/templates/catalog/`, rendered by
`lab_ui/catalogs/standard.py`: the tests render it and read it, and run nothing of what they render except the two shell
blocks of the `lab` action and of the job `result`, which they take out of the managed files and run with bash on hostile inputs.
The tests that need bash, jq, make or Compose 5 print a visible skip line when they do not find it.
