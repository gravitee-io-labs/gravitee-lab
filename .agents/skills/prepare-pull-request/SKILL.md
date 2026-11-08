---
name: prepare-pull-request
description: Prepares a Gravitee Lab change for review by running the checks that match it and printing the pull request description.
---

# Prepare a pull request

Run this before a change leaves the machine, in the repository that holds it: the Lab, or a catalog. It checks and prints;
it never pushes or opens a pull request unless the person asks. `CONTRIBUTING.md` has the workflow, and
`.github/PULL_REQUEST_TEMPLATE.md` the description.

1. See what changed: `git status --short`, then `git diff --stat` for the changes not staged yet and `git diff --cached
   --stat` for the ones staged. Group them: an add-on, a setup or a playground (they are in a catalog, not in the Lab);
   Python (`lab-ui/`, `seeder/`); an app of a playground; the Lab UI's page; documentation.
2. Run the checks that match, from the table of `CONTRIBUTING.md` ("Before you open a pull request"), and read the output of
   each: a skipped test did not pass. `make smoke` and starting an item need the person's lab: ask before you start or stop
   anything, and say what you could not run.
3. Confirm that nothing private is in the change:

   ```sh
   git diff --cached --name-only | grep -E '(^|/)(\.env|\.lab|plugins|local|secrets|\.state)(/|$)'
   ```

   It prints nothing. No `.env`, `.lab/`, `plugins/`, `local/`, `secrets/` or `.state/` path is staged, and no license,
   key or certificate. Unstage what it prints with `git restore --staged <path>`. A commit has the person's own name and
   email, and no tool as author, co-author or credit.
4. For a new add-on, setup or playground, confirm that the documentation that describes what changed is up to date. For a
   playground, `tested` holds the versions it was checked on, and the versions and
   the time of its first start go under "Tried on" in the description. CI starts it on each set once a maintainer has
   approved the run (`CONTRIBUTING.md`, "What CI does"): say so.
5. Print the description. Read `.github/PULL_REQUEST_TEMPLATE.md` and fill its fields: under "What and why", what the
   change does and why, with the issue it closes if there is one. Under "Checklist", tick only what you did in this run.
   Print the result for the person to paste.
6. Stop there. When the person asks you to propose it, fork the repository if you cannot push, and follow `CONTRIBUTING.md`
   for the branch, the commit message and the pull request. A branch that the Lab UI's Share made is already pushed
   (`lab/<id>-<yyyymmdd>`): open the pull request from it.
