# Recorded replies for `repo-settings`

Replies of GitHub for the tests of `lab_ui/catalogs/settings.py` and `fleet.py`. No test reaches GitHub: a transport serves
these tables (`tests/catalogs/test_settings.py`, class `Hub`), records every call, and answers a 404 for a path that is not here.

| File | What it is |
|---|---|
| `user-private-free.json` | The personal Free private repository of the standard (private, owned by a user): no rules, no rulesets, no environments, no secret scanning, nothing pinned. The replies that were recorded are the real ones with the names changed; the others (`topics`, `has_wiki`, `allow_auto_merge`, `allow_update_branch`, the text of the 422 on the organization endpoints, the names of the workflows, the Dependabot secrets) are filled in from the shapes GitHub documents |
| `public-standard.json` | A public repository of an organization with every setting of the standard. Built from the documented shapes: it is not a recording |
| `org-private.json` | A private repository of an Enterprise organization with every setting of the standard, the push rules and the protected paths included, and the organization endpoints (secrets, variables, runner groups). Built from the documented shapes: it is not a recording |
| `rulesets/*.json` | The four managed rulesets of the standard (4.3), as the Lab's template holds them. The template is written by the package of the standard (`lab-ui/templates/catalog/managed/.github/rulesets/`); these copies are the test data of the module that applies them, so that its tests do not move with the template. A test keeps them equal to the shapes of 4.3 |

A file is `{"_about": text, "repo": "OWNER/REPO", "replies": {"METHOD path": {"code": status, "body": JSON}}}`. A list is named
without its paging (`per_page`, `page`), and a path with a query keeps the rest of it (`collaborators?permission=push`). A
status 200 with a `null` body is what `gh api` shows for the 204 of GitHub. Names are `acme` and `acme-labs`; no repository, person,
secret or address here is real.

The rehearsal of the standard (R-private and R-rules) replaces the built replies by recorded ones.
