# Maintaining the repository

For the people who own the repository: moving or forking it, the one-time GitHub settings, and the pins to review by
hand. Contributors do not need this page: they read [CONTRIBUTING.md](../CONTRIBUTING.md).

## Moving or forking the repository

The owner and the repository name appear in clone and image addresses, in the documentation, and in the Lab UI.
`NOTICE` keeps the initial repository on purpose: its attribution term requires it. This sweep rewrites every other
file, this page included. Replace `OWNER/REPO` with the new owner and repository in lowercase, because image names must
be lowercase:

```bash
git grep -l 'gravitee-io-labs/gravitee-lab' -- . ':!NOTICE' | xargs sed -i.bak 's|gravitee-io-labs/gravitee-lab|OWNER/REPO|g'
```

```bash
find . -name '*.bak' -not -path './.git/*' -delete
```

A catalog pins the Lab by the commit of a release (`lab.ref` in its `.github/standard.json`, a commit that a tag `vX.Y.Z` on `main`
names: SD11), and its CI fetches that commit. When the history of this repository is rewritten or the repository is moved, the
pins point at nothing: move them to a release of the new history, `python3 lab-ui/catalog.py upgrade --fleet OWNER --lab-ref COMMIT`,
which opens a pull request on each catalog of the owner.

Then do these one-time steps on GitHub:

1. Make the `ghcr.io` packages public, so that `always` pull policies work without a login.
2. Enable branch protection on `main`: require a pull request and the CI jobs, `E2E result` among them ([End-to-end runs](#end-to-end-runs)).
3. Enable private vulnerability reporting (Settings > Code security), which [SECURITY.md](../SECURITY.md) relies on.
4. Enable secret scanning with push protection, and Dependabot alerts (Settings > Code security).
5. Require full-SHA pinning of actions, and approval of workflow runs from outside collaborators (Settings > Actions >
   General).
6. Delete the `ghcr.io` packages, Actions runs and caches that the repository does not use.
7. Decide on a `CODEOWNERS` file and a code of conduct. Both are the owner's choice.
8. Check that the `REPOSITORY` constant in `lab-ui/web/js/dialogs/welcome.js` has the new address. The sweep rewrites it.

A fork builds the images only when you run its *Lab images* workflow by hand, from `main`.

## End-to-end runs

The **E2E** workflow starts the canary of the default catalog on the engine as a pull request leaves it, and every night on `main`; each
catalog runs the same test (`e2e-run.yml`) for its own changes. It proves what the checks cannot: that the thing works. Its jobs and what they prove are in
[CONTRIBUTING.md](../CONTRIBUTING.md#what-ci-does). Two things need you, once.

### What it needs

1. **A CI license.** Make one for the purpose, not your own: short-lived, with the features the playgrounds need and no more, revocable
   alone, so that replacing it is a few minutes of work if it ever leaks. It reaches only the step that writes it to the runner's `.env`
   (a test of `.github/tests` keeps it so), and GitHub masks it in the logs.
2. **Two environments** (Settings > Environments), each with the secret `GRAVITEE_LICENSE`:
   - `e2e`: *Required reviewers* (maintainers; keep *Prevent self-review* on). Every job that runs the code of a pull request waits here.
     Leave the deployment branches unrestricted: a pull request runs on its merge reference, which a branch rule would refuse.
   - `e2e-nightly`: no reviewer, deployment branches limited to `main`. It runs the nightly job, which is the code of `main` alone.
   - Optionally, in both, `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` (a read-only access token): Docker Hub counts what an address pulls
     and the runners share theirs, so that a heavy day can hit its limit.

   `e2e.yml` passes these secrets by name to `e2e-run.yml`: GitHub gives a called workflow an environment secret only when its caller
   names it, and an empty one otherwise, with no error.
3. **Actions settings** (Settings > Actions > General): *Require approval for all outside collaborators*, and leave *Send write tokens* and
   *Send secrets to workflows from pull requests* off. Keep the default workflow permission at *Read repository contents*.
4. **Branch protection** on `main`: require two checks, `CI result` and `E2E result`. `CI result` passes when every job of the CI
   workflow ended in success. `E2E result` fails when anything ended wrong, passes when there was nothing to try (documentation, say),
   and waits while a maintainer has not approved.
5. **A `CODEOWNERS` file** that sends `.github/`, `docker-compose.yml`, `seeder/`, `lab-ui/` and `Makefile` to the maintainers, so that a
   change to what runs with the license is read by one of them. The names are yours to give:

   ```
   /.github/             @OWNER/maintainers
   /docker-compose.yml   @OWNER/maintainers
   /Makefile             @OWNER/maintainers
   /seeder/              @OWNER/maintainers
   /lab-ui/              @OWNER/maintainers
   ```

A runner has 4 CPUs and 16 GB of memory in a public repository, enough for the playgrounds that ask for 10 GB. Disk is the tighter limit:
the job removes what the runner comes with and the lab does not use before it starts. If a job runs out of one or the other, use a larger
runner for the `e2e` job (`runs-on`).

### Approving a run

A pull request from this repository's own branches starts the jobs by itself, and the `e2e` jobs wait: open the run, *Review deployments*,
choose `e2e`, approve. **Read what you approve first**: the items, their Dockerfiles, seeds and Compose files, and the changes to `.github/`,
the `Makefile`, `seeder/` and `lab-ui/`. The workflow file a run uses is the one of the pull request, so a change to it is a change to
what runs with the license. Approving all the waiting jobs of a run takes one click.

A fork's pull request gets the jobs that need no secret. For the rest, after you have read it, push its commit to a branch of this
repository: the same jobs run on that commit, and their checks, `E2E result` included, show on the pull request.

```bash
git fetch origin pull/<number>/head
git push origin FETCH_HEAD:refs/heads/e2e/<number>
```

Delete the branch when the run ends (`git push origin --delete e2e/<number>`), and do it again, after reading them, for any commit the author
pushes later. To run by hand, for the main branch or another of this repository: Actions > E2E > *Run workflow*, with `canary` or
items as `kind:id` separated by spaces.

### Reading a result

The page of a job lists what was tried and how long each step took, and under a failure the log of each container that was not up. An item
that cannot be tried in CI (it needs a setting only a person gives, a token for instance) says *not tried*, with a warning: try it by hand.
A night that fails opens an issue with the label `e2e-nightly`, or adds to the one that is open. The usual causes are a moving tag that
changed (`latest`, `amp-demo-latest`), a product release that changed an API a seed calls, and a registry that was down: the log says which.

## Publishing a version

Every merge to `main` is a version: the *Release* workflow tags the merge `vX.Y.Z` and publishes its release on GitHub, with the
pull requests merged since the version before. The Lab UI of every clone sees it at its next check and offers the update. Name
each pull request for the people who read it there: its title is a line of what the version changes.

Before merging, give the pull request the label of the number it raises:

| Label | Raises | For |
| --- | --- | --- |
| `major` | `2.0.0` | An update that needs a person to do something by hand |
| `minor` | `1.1.0` | A new feature |
| `catalog-format` | `1.1.0` | Something catalogs can use: a key, a label, a seed folder, a core service ([the contract](contract.md)) |
| none | `1.0.1` | A fix, and anything else |

A pull request that adds something catalogs can use gets `catalog-format`, with `major` too when it also needs people to act.
Its release lists it under *For catalogs*: that section is what a catalog may use from that version on, and an author who uses it
moves the catalog's pin to that release with `catalog.py upgrade --lab-ref latest`. GitHub lists a pull request under the first
section whose label it has, and only there: one labeled `major` and `catalog-format` is under *Changes that need you to do
something*, which comes first so that what needs people to act is never missed. Its title then says what catalogs can use.

When several pull requests are merged while a release is still running, the next release counts them all and raises the highest
number their labels ask for. A run that runs again does no harm: a commit that a version holds makes no second one, and
the release of a tag that has none is made then. Only the tags `vX.Y.Z` are versions; a tag of another form (`v1.6.0-rc1`) is left alone. A tag is never moved once pushed: a clone that updated to it keeps it, and the Lab UI offers only
the tags that are on `main`.

The workflow pushes the tag with a deploy key, since the token of a workflow cannot be let past a ruleset. Set it up once:

1. **The key**: an SSH key made for the purpose (`ssh-keygen -t ed25519 -N "" -f release`), its public half added under
   Settings > Deploy keys with *Allow write access*, its private half as the secret `RELEASE_DEPLOY_KEY` of the environment
   `publish`, then both files deleted. Only the step that pushes the tag reads it (a test of `.github/tests` keeps it so).
2. **Two tag rulesets** on every tag: `catalog-tags` forbids deleting or moving a tag, with administrators alone as bypass, and
   `release-tags` forbids creating one, with administrators and deploy keys as bypass.
3. **The labels** `major`, `minor` and `catalog-format`.

To replace the key, make a new one, set the secret again and delete the old deploy key: nothing else changes.

## Pins to review by hand

Dependabot updates the actions, the Python requirements and the Dockerfiles. A few pins sit in text it does not read:
the header of `.github/workflows/ci.yml` lists them.
