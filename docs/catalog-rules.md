# Reading a failed check

Every rule that `catalog.py check` and the stages of `catalog.py ci` can print, with what it means and how to fix it. `make explain CODE=ST05` prints the same for one code. A finding is an error, which fails the check, or a warning, which says what to look at.

## Families

| Family | Codes |
|---|---|
| [Managed files](#managed-files) | `SD01` to `SD11` |
| [Structure](#structure) | `ST01` to `ST23` |
| [Secrets](#secrets) | `SE01` to `SE07` |
| [Commits](#commits) | `CR01` to `CR02` |
| [Workflows](#workflows) | `WF01` to `WF21` |
| [Dockerfiles](#dockerfiles) | `DF01` to `DF24` |
| [Guides](#guides) | `GD01` to `GD05` |
| [Code owners](#code-owners) | `CO01` |
| [Images](#images) | `IM01` to `IM04` |
| [Build stage](#build-stage) | `BD01` to `BD06` |
| [Manifests stage](#manifests-stage) | `MF01` to `MF02` |
| [Test stage](#test-stage) | `TS01` to `TS02` |
| [Needs stage](#needs-stage) | `ND01` to `ND02` |
| [Smoke stage](#smoke-stage) | `SM01` to `SM03` |
| [Publish gate](#publish-gate) | `PB01` to `PB08` |
| [End-to-end plan](#end-to-end-plan) | `E2E01` to `E2E05` |
| [Links stage](#links-stage) | `LK01` |
| [Scan stage](#scan-stage) | `SC01` to `SC02` |
| [The Lab's trust policy](#the-labs-trust-policy) | `T01` to `T29` |
| [The notes of the trust policy](#the-notes-of-the-trust-policy) | `N01` to `N05` |

## Managed files

### SD01

*Error.* A managed file is missing.

- **Message**: {path} is missing from the shared files. Fix: catalog.py upgrade --write.

### SD02

*Error.* A managed file differs from the Lab's render at the repository's own level.

- **Message**: {path} was edited by hand. Fix: git checkout -- {path}. A change to a shared file is read by a maintainer.

### SD03

*Warning.* A managed file is behind: the repository's level is below the Lab's (an error when that level is urgent).

- **Message**: {path} is from standard {have}; this Lab has {now}. Fix: catalog.py upgrade --write.

### SD04

*Error.* Standard.json is unreadable, has a bad value, or is over 4 KB (a warning for an unknown key).

- **Message**: .github/standard.json: {problem}.

### SD05

*Warning.* The repository's level is above the Lab's.

- **Message**: .github/standard.json says standard {n}; this Lab knows {m}. Fix: update the Lab with git pull, then run again.

### SD06

*Error.* The level went down, or a managed file lost its marker in this change (--base).

- **Message**: {what}. Fix: undo that change; the level only goes up and a managed file keeps its marker.

### SD07

*Error.* The Lab could not be fetched (CI).

- **Message**: The Lab could not be fetched. This is the repository's settings, not your change: a maintainer will run it again. Fix (maintainers): while the Lab is private this repository needs the secret LAB_DEPLOY_KEY (also as a Dependabot secret), and .github/standard.json must name a commit that exists.

### SD08

*Info.* A managed path has no marker: the catalog owns it.

- **Message**: {path} is owned by this catalog; upgrade skips it. WF01 to WF21 still apply.

### SD09

*Warning.* The catalog owns dependabot.yml and it lacks github-actions or a cooldown.

- **Message**: .github/dependabot.yml has no {ecosystem}. Fix: add it.

### SD10

*Error.* The Makefile does not include .catalog/standard.mk.

- **Message**: Makefile does not include .catalog/standard.mk. Fix: add the line: include .catalog/standard.mk

### SD11

*Error.* Lab.ref is not the commit of a release of the Lab (a tag vX.Y.Z on its main) (CI).

- **Message**: The Lab commit {sha7} is not a release of the Lab. Fix: pin the latest release (catalog.py upgrade --lab-ref latest).

## Structure

### ST01

*Error.* Symbolic link (git mode 120000).

- **Message**: {path} is a symbolic link. Fix: commit the file itself; the Lab does not install links.
- **Why**: A link can lead out of the catalog to a file of whoever reads the folder, and a tool that follows it reads that file. A catalog is made of files.

### ST02

*Error.* Submodule (160000).

- **Message**: {path} is a submodule. Fix: copy what you need into the catalog; the Lab installs no submodule.
- **Why**: A submodule is another repository that the catalog does not control and that a reader's git would fetch.

### ST03

*Error.* A path segment outside [A-Za-z0-9._+-], equal to . or .., empty; a backslash or a control character.

- **Message**: {path} has a name the Lab cannot install. Fix: use letters, digits and . _ + - only.
- **Why**: A name with a character outside the safe set cannot be written the same on every system, and a `..` or a backslash in a path is how a file is made to land outside its folder.

### ST04

*Error.* Depth over 8, or a path over 200 characters.

- **Message**: {path} is too deep or too long. Fix: at most 8 folders and 200 characters.
- **Why**: A path of more than 8 folders or 200 characters fails on some systems and is a sign of an archive bomb.

### ST05

*Error.* .env, or .env.* other than .env.example.

- **Message**: {path} is an environment file. Fix: git rm --cached {path}, add it to .gitignore, and commit a .env.example with defaults. If it held a real secret, revoke the secret first: removing the file does not remove it from the history.
- **Why**: An environment file holds what only one machine knows, often a secret. Removing the file from the tree does not remove it from the history, so the secret has to be revoked.

### ST06

*Error.* Secrets/, .state/, or a name ending .key .pem .p12 .pfx .jks .keystore .crt .csr .srl.

- **Message**: {path} looks like a key, a certificate or machine state. Fix: make it with a script at start and ignore it. If it is real, revoke it first.
- **Why**: A key, a certificate or the state of a machine does not belong in a repository that others clone. A demo certificate is made by a script at start.

### ST07

*Error.* An agent-instruction file inside a unit (AGENTS.md CLAUDE.md GEMINI.md, .claude/ .agents/ .gemini/ .cursor* .windsurf*, .github/copilot-instructions.md).

- **Message**: {path} is an agent instruction file inside an item. Fix: keep it at the top of the catalog or in the kind folder; the Lab never installs it.
- **Why**: An agent instruction file inside an item would be read by the agent of whoever installs it and steer it. They belong at the top of the catalog or in the kind folder, where the owners wrote them.

### ST08

*Warning.* A path outside what the Lab reads and what the standard lists.

- **Message**: {path} is not in a place the Lab reads. Fix: if it should be installed, move it under addons/, setups/ or playgrounds/; if it is for maintainers, ignore this.
- **Why**: The Lab reads `catalog.yml` and the three folders of units; anything else stays in the repository. A path elsewhere may be a mistake, so it is said once.

### ST09

*Error.* A file over 1 MB (.yml .yaml .md .json) or 2 MB (any other).

- **Message**: {path} is {n} KB, the limit is {m} KB. Fix: shrink it, or link to it.
- **Why**: A catalog is read and installed whole, and a large file makes it slow for everyone.

### ST10

*Error.* A unit over 2000 files or 25 MB; a catalog over 500 units, 10000 files or 100 MB.

- **Message**: {unit} holds {found}, the limit is {limit}. Fix: split it.
- **Why**: A unit or a catalog over the limits (2000 files or 25 MB for a unit, 500 units, 10000 files or 100 MB for a catalog) is too large to be read quickly and safely.

### ST11

*Error.* A zero-width, bidirectional or other invisible format character in a text file (Trojan Source).

- **Message**: {path}:{line} holds an invisible character ({char}). Fix: remove it; a reader cannot see it.
- **Why**: An invisible or bidirectional character makes source read differently from how it runs (Trojan Source), and a reviewer cannot see it.

### ST12

*Warning.* CRLF in a text file other than Markdown.

- **Message**: {path} uses CRLF line endings. Fix: use LF; .gitattributes does for new files.
- **Why**: Mixed line endings make a diff noisy and break scripts.

### ST13

*Error.* Two paths equal when case is folded.

- **Message**: {path} differs from {other} only by case. Fix: rename one; a Mac cannot hold both.
- **Why**: Two paths that differ only by case cannot both be written on a Mac or on Windows, so the second would overwrite the first.

### ST14

*Error.* A unit folder or setup file name outside [a-z0-9-]+, or `local`.

- **Message**: {path}: a unit is named with lowercase letters, digits and dashes, and not `local`.
- **Why**: An item is found by the name of its folder, and the names of items are lowercase letters, digits and dashes. `local` would read as the items of this machine.

### ST15

*Error.* A folder directly under addons/ or playgrounds/ without compose.yml, or a file under setups/ that is not setups/&lt;name>.yml.

- **Message**: {path} is not a complete unit. Fix: add compose.yml, or delete the folder.
- **Why**: An item is its `compose.yml` and what it mounts; a folder without one is not an item, and a file loose in a kind folder belongs to none.

### ST16

*Error.* A program or an archive (ELF, Mach-O, PE, zip, tar, gzip, jar, by magic number; images, fonts and PDFs are allowed).

- **Message**: {path} is a program, not source. Fix: add the source and build it in a Dockerfile.
- **Why**: A program or an archive in a catalog cannot be read by a reviewer. A unit is source, built in a Dockerfile.

### ST17

*Error.* A required file is missing (a warning for .github/PULL_REQUEST_TEMPLATE.md).

- **Message**: {file} is missing. Fix: catalog.py upgrade --adopt adds what is missing.
- **Why**: Every catalog has the same files, so that contributors, reviewers and the tools find them in one place.

### ST18

*Error.* LICENSE does not match catalog.yml license.

- **Message**: LICENSE does not match license: {spdx} in catalog.yml. Fix: put the text of {spdx} in LICENSE.
- **Why**: The license says what a contributor publishes under and what a person may do with the catalog.

### ST19

*Error.* Git LFS (filter=lfs in .gitattributes).

- **Message**: .gitattributes uses Git LFS. Fix: remove it; the Lab installs no LFS file.
- **Why**: Git LFS stores a pointer in the repository and the file elsewhere, which the Lab does not fetch.

### ST20

*Warning.* Code in seed/** (.py .sh .js .rb, or an executable bit).

- **Message**: {path} is code. A seed brings data; code makes this item "Brings its own seed code", and it cannot use a Cloud Token.
- **Why**: A seed is data. Code in it runs with the Lab's seeder, so the Lab treats the item as one that brings its own seed code.

### ST21

*Error.* A secret-scanner configuration or ignore file anywhere in the catalog.

- **Message**: {path} configures a secret scanner. Fix: remove it; the scan that judges this change uses the Lab's own configuration, and a maintainer decides what it may skip.
- **Why**: A pull request must not choose what the scan that judges it may skip. The scan uses the Lab's own configuration, and a maintainer decides about exceptions.

### ST22

*Error.* A file that runs on the machine of whoever opens the folder.

- **Message**: {path} would run on a reader's machine. Fix: remove it; a unit is Compose files, data and the sources of its apps.
- **Why**: An editor task, a hook or a development container runs on the machine of whoever opens the folder, so a pull request could run code on a reviewer's machine.

### ST23

*Error.* A unit folder or setup file not named after the unit's x-gravitee.name: lower case, every run of characters other than a-z and 0-9 one dash, no dash at either end.

- **Message**: The folder of {name} is {folder}; its name says {slug}. Fix: rename the folder to {slug}. The folder is the item's id: read Renaming an item in docs/writing-a-catalog.md first.
  The file of {name} is {folder}; its name says {slug}. Fix: rename the file to {slug}.yml. The file is the item's id: read Renaming an item in docs/writing-a-catalog.md first.
- **Why**: A person finds an item by its name, and the Lab finds it by its folder: its id, what other items include, the prefix of its settings, its stars and its records. One name for both is what a reader expects and what the Lab UI makes for a new item.

## Secrets

### SE01

*Error.* A private key header (-----BEGIN ... PRIVATE KEY-----).

- **Message**: {path}:{line} holds a private key. Fix: revoke it first, it is public in this pull request. Then remove it from the commits of this branch: git reset --soft $(git merge-base origin/main HEAD), commit again, and force-push your branch.
- **Why**: A private key in a public repository is a leaked key. It is public in the pull request as soon as it is pushed, even if a later commit removes it.

### SE02

*Error.* A token pattern: GitHub, AWS access key, Slack, Google API key, a JWT triple, an sk- key.

- **Message**: {path}:{line} looks like {kind}. Fix: make it a setting (${PREFIX_NAME}) and keep the value in .env. If it is a real token, revoke it first, then remove it from the commits of this branch (git reset --soft $(git merge-base origin/main HEAD), commit again, force-push).
- **Why**: These token shapes are what the services they belong to hand out. One in a repository is public.

### SE03

*Error.* A finding of the pinned gitleaks container over BASE..HEAD, run with the Lab's configuration.

- **Message**: {path}:{line} matches the rule {rule} (the secret is redacted). Fix: revoke it first, then remove it from the commits of this branch: git reset --soft $(git merge-base origin/main HEAD), commit again, and force-push your branch.
- **Why**: Gitleaks is the broad net over the commits of the change, run with the Lab's own configuration.

### SE04

*Error.* A literal secret value in a Compose file or .env.example.

- **Message**: {path}:{line}: {name} has a literal value. Fix: write ${NAME:-} and set the value in .env.
- **Why**: A password, secret, key or token written in a Compose file is edited into a shared file by whoever changes it. A setting with a default is the same for a demo and can be changed in `.env`.

### SE05

*Error.* A URL with a password (scheme://user:secret@host): an error in Compose files and seed/**, a warning elsewhere.

- **Message**: {path}:{line} holds a URL with a password. Fix: take the password from a setting.
- **Why**: A password in a URL is a password in the file. In a Compose file or a seed it is used, in a guide it is usually an example.

### SE06

*Error.* A private identifier: a registry host, an image prefix, a name or an address of any catalog this Lab has added (Share, target Public).

- **Message**: {path}:{line} names {identifier}, which belongs to a private catalog. Fix: remove it, or share to a Private catalog.
- **Why**: A catalog shared to a Public catalog must not name a private one: its registry, its image prefix or its address.

### SE07

*Error.* The text gitleaks:allow, or another scanner's allow marker.

- **Message**: {path}:{line} carries a scanner allow marker. Fix: remove it; a maintainer decides what the scan may skip.
- **Why**: An allow marker in the catalog would silence the scan in the run that judges it.

## Commits

### CR01

*Error.* A commit of BASE..HEAD whose author, committer or Co-authored-by trailer names a tool.

- **Message**: commit {sha7}: its {role} names a tool. Fix: rewrite the commit under your own name (git commit --amend --reset-author for the last one), or squash your branch into one commit of yours.
- **Why**: A squash merge copies names into the message of `main`. A tool's name as an author, a committer or a co-author would travel from a branch into `main`.

### CR02

*Warning.* A commit message of BASE..HEAD with a tool credit line.

- **Message**: commit {sha7}: its message carries a tool credit. The squash drops it from main. Fix: edit the message if you want it gone.
- **Why**: A credit line in a message is noise in a history people pin. The squash drops it from `main`.

## Workflows

### WF01

*Error.* Events are only `pull_request`, `push`, `schedule`, `workflow_dispatch`, `workflow_call`.

- **Message**: {file}: the trigger {event} is not allowed. Use pull_request: pull_request_target and workflow_run run with secrets on content from outsiders.

### WF02

*Error.* Every `uses:` is `./...` or `owner/repo[/path]@&lt;40 hex>`.

- **Message**: {file}:{line}: {action} is not pinned to a full commit. Pin it as owner/repo@&lt;40 hex> # tag; Dependabot proposes the next one.

### WF03

*Error.* The top level says `permissions:` then `contents: read` and nothing else.

- **Message**: {file}: give the token the least it needs: permissions: contents: read at the top.

### WF04

*Error.* No `${{` inside a `run:`.

- **Message**: {file}:{line}: ${{ }} inside run:. Pass the value through env: and read the variable; a title or a branch name would become a command.

### WF05

*Error.* Every job has `timeout-minutes` (a job that calls a reusable workflow is exempt).

- **Message**: {file}: job {job} has no timeout-minutes. Add one, so that a stuck build ends.

### WF06

*Error.* `runs-on` is `ubuntu-24.04`, `ubuntu-24.04-arm`, or exactly `${{ matrix.runner }}` where the job's `matrix` is `fromJSON(needs.&lt;job>.outputs.matrix)` of a job of the same file, or lists only those two labels.

- **Message**: {file}: job {job} runs on {label}. Use ubuntu-24.04 or ubuntu-24.04-arm, or a matrix that the plan job of this file makes: a self-hosted runner keeps what a pull request leaves on it.

### WF07

*Error.* Every `actions/checkout` says `persist-credentials: false`.

- **Message**: {file}:{line}: actions/checkout leaves its token in .git. Add persist-credentials: false.

### WF08

*Error.* Each secret is read only where a rule allows: `LAB_DEPLOY_KEY` as the `key:` of the `lab` action or as the same-named entry of a caller's `secrets:`; `GITHUB_TOKEN` or `REGISTRY_TOKEN` as the `password:` of `docker/login-action` in `publish.yml` (and `catalog-publish.yml`, its reusable counterpart in the Lab); `GRAVITEE_LICENSE` in exactly one step of `e2e.yml` and of `versions.yml`; `VERSIONS_DEPLOY_KEY` in the step of `versions.yml` that pushes (`git push --atomic`) and runs no `catalog.py`; `GRAVITEE_REGISTRY_USERNAME` and `GRAVITEE_REGISTRY_PASSWORD` in the step of `e2e.yml` and of `versions.yml` that logs in to `graviteeio.azurecr.io` and runs nothing of the Lab. Never `secrets: inherit`.

- **Message**: {file}:{line}: secrets.{name} is read where no rule allows it. Pass it by name to the one step that needs it.

### WF09

*Error.* Write permissions only where the file may have them: none in `ci.yml` and `e2e.yml`; `issues` in the job `report` of `nightly.yml` and of `versions.yml`; `packages` in `images` and `attest`, `id-token` and `attestations` in `attest`, `contents` in `release`, of `publish.yml`; in the reusable form the single call job of `publish.yml` holds their union.

- **Message**: {file}: job {job} holds {permission}: write. Only publish.yml's images, attest and release jobs, and the report jobs of nightly.yml and versions.yml, may write.

### WF10

*Error.* A job with steps and `packages: write` declares an `environment` (a call job of the reusable form is exempt: the jobs of its callee are checked).

- **Message**: {file}: job {job} writes packages without an environment. Add environment: publish.

### WF11

*Error.* A `concurrency:` group exists (not in a reusable file).

- **Message**: {file}: add a concurrency group, so that a push does not pile runs up.

### WF12

*Error.* No download piped into a shell.

- **Message**: {file}:{line}: a download is piped into a shell. Download it, check a digest, then run it.

### WF13

*Error.* `id-token: write` appears in the job `attest` of `publish.yml` and `catalog-publish.yml` only, and no step exports the runtime token of Actions (`ACTIONS_RUNTIME_TOKEN`, `ACTIONS_CACHE_URL`, `ACTIONS_RESULTS_URL`, `ghaction-github-runtime`).

- **Message**: {file}: the identity token and the runtime token of Actions go to the attest job of publish.yml only.

### WF14

*Error.* No `continue-on-error: true`.

- **Message**: {file}:{line}: continue-on-error hides a failure. Make the step decide its own exit code.

### WF15

*Error.* Reusable form: the Lab repository and commit named by a caller equal `lab.repository` and `lab.ref` of `standard.json`.

- **Message**: {file}: the Lab pinned here is not lab.ref of .github/standard.json. Fix: catalog.py upgrade --lab-ref {ref}.

### WF16

*Error.* Reusable form: a caller passes `LAB_DEPLOY_KEY` and nothing else.

- **Message**: {file}: pass the Lab key by name and nothing else; secrets: inherit hands over every secret.

### WF17

*Error.* No cache of any kind: no `actions/cache`, no `cache:` input of a `setup-*` action, no `cache-from` or `cache-to`, no `type=gha` or `type=registry`.

- **Message**: {file}:{line}: a cache lets a pull request write what main later reads. Remove it.

### WF18

*Error.* In `publish.yml` and `catalog-publish.yml` a `needs.&lt;job>.result` is compared with `== 'success'` or `contains(fromJSON('["success","skipped"]'), ...)`, never with `!=` (a canceled or timed-out job is neither a failure nor a success).

- **Message**: {file}:{line}: needs.{job}.result is tested with !=. Fix: name what you accept: contains(fromJSON('["success","skipped"]'), needs.{job}.result).

### WF19

*Error.* No write to `$GITHUB_ENV` or `$GITHUB_PATH`, and no `${{` inside the `script:` of `actions/github-script`.

- **Message**: {file}:{line}: this lets data from outside choose what runs next. Fix: pass the value through env: to the one step that needs it.

### WF20

*Error.* No `ACTIONS_ALLOW_UNSECURE_COMMANDS`; no `run-id` or `github-token` on `actions/download-artifact` (an artifact of another run is data that someone may have written); no dynamic access to secrets: `secrets[`, `toJSON(secrets)`, `toJson(secrets)`.

- **Message**: {file}:{line}: {what}. Fix: name the one secret a step needs, and download only the artifact of this run.

### WF21

*Error.* Fail closed. The rules read by indentation, so a workflow that hides what they read is refused: a flow mapping or sequence as the value of `on`, `permissions`, `secrets`, `runs-on`, `uses` or `environment`; `permissions: write-all` or `read-all`; an anchor, an alias or a tag (`&x`, `*x`, `!!x`); a quoted key.

- **Message**: {file}:{line}: this is written so that the rules cannot read it. Fix: write it in block style, one key per line.

## Dockerfiles

### DF01

*Error.* # syntax= names something other than docker/dockerfile[:tag].

- **Message**: {path}:{line}: # syntax= names another frontend. A build frontend is a program that runs in the build. Use docker/dockerfile or remove the line.
- **Why**: A build frontend is a program that runs in the build with its inputs.

### DF02

*Error.* ADD of a URL or a .git address without --checksum=.

- **Message**: {path}:{line}: ADD of a URL without --checksum. Fix: download it in a RUN and check its digest, or add --checksum=sha256:...
- **Why**: A file or a repository fetched at build time with no checksum changes without a commit.

### DF03

*Error.* Curl or wget piped to sh, bash, python, perl, node or sudo.

- **Message**: {path}:{line}: a download is piped into a shell. Fix: download it, check a digest, then run it.
- **Why**: A download that is run as it arrives is whatever the server sends that day.

### DF04

*Error.* RUN --security=insecure, --network=host, --mount=type=ssh, --mount=type=secret.

- **Message**: {path}:{line}: a build is given no key and no host access. Fix: remove the option.
- **Why**: A build is given no key and no host access: with them a build step could read the builder's secrets or reach its machine.

### DF05

*Error.* ENV or ARG with a secret-looking name and a literal value.

- **Message**: {path}:{line}: {name} has a literal value. Fix: take it as a setting at start, not in the image.
- **Why**: A secret written in the image is in every layer and every copy of it.

### DF21

*Warning.* A base image with no tag, or :latest.

- **Message**: {path}:{line}: the base image {image} has no version. Fix: pin a tag.
- **Why**: A base image with no version changes under the catalog without a commit.

### DF22

*Warning.* A base image not pinned by digest.

- **Message**: {path}:{line}: the base image {image} is not pinned by digest. Dependabot keeps name:tag@sha256:... current.
- **Why**: A digest makes a base image exactly one image; Dependabot keeps `name:tag@sha256:...` current.

### DF23

*Warning.* The final stage runs as root.

- **Message**: {path}: the final stage runs as root. Fix: add USER.
- **Why**: An app that runs as root has more power over its container than it needs.

### DF24

*Warning.* COPY --from=&lt;an external image>.

- **Message**: {path}:{line}: copies from the image {image}. It is listed in the summary with the base images.
- **Why**: Files copied from another image are part of the image, and a reviewer reads them with the base images.

## Guides

### GD01

*Error.* A relative link that does not resolve inside the catalog.

- **Message**: {path}:{line}: the link {target} does not resolve inside the catalog. Fix: link to a file that is there.
- **Why**: A broken link in a guide is a broken guide, and a link that leaves the catalog leads nowhere once it is installed.

### GD02

*Error.* A javascript:, data: or vbscript: URL.

- **Message**: {path}:{line}: a {scheme}: link. Fix: use https or a relative path.
- **Why**: A `javascript:`, `data:` or `vbscript:` link runs code in the origin that shows the guide.

### GD03

*Error.* Raw &lt;script, &lt;iframe, &lt;object, &lt;embed, &lt;form, an on*= attribute, srcdoc&#61;, data&#58;text/html, outside fenced code and inline code.

- **Message**: {path}:{line} holds {what}. A guide is text and diagrams; the Lab runs nothing a guide brings. Fix: show it as code (a fenced block) or remove it.
- **Why**: A guide is text and diagrams. Raw HTML that runs (a script, a frame, a form, an event handler) is code in the Lab's origin, which can drive Docker.

### GD04

*Error.* An image source that is neither relative nor https:

- **Message**: {path}:{line}: the image {src} is not relative or https.
- **Why**: An image that is not relative or https is fetched from somewhere a reader did not choose, or carries its own content.

### GD05

*Error.* An SVG file, or a data:image/svg source, that holds &lt;script, &lt;foreignObject, &lt;iframe, a &lt;use with an external href, an on*= attribute or javascript:

- **Message**: {path} is an SVG with active content ({what}). Fix: export it as a plain image, or remove the element.
- **Why**: An SVG is a document that can run a script. The Lab shows it as a plain image, but an SVG with active content is a mistake or an attack.

## Code owners

### CO01

*Error.* The file lacks the line * or the line /.github/, a line names no owner, or an owner is not @user or @organization/team (ASCII only).

- **Message**: .github/CODEOWNERS: {path} has no owner. Fix: add the line: {path} {maintainers}
  .github/CODEOWNERS: {value} is not a GitHub name. Fix: write @name or @organization/team.

## Images

### IM01

*Error.* A service that builds an image has no pull_policy (the seeder's service too).

- **Message**: {path}: {service} builds an image but has no pull_policy. Fix: pull_policy: ${ID_PULL_POLICY:-build}.
- **Why**: `pull_policy: ${ID_PULL_POLICY:-build}` lets a person choose to pull the published image or build it here; without it Compose pulls or builds by its own rule.

### IM02

*Error.* The image of a built service is outside the images prefix of catalog.yml, or two units build the same name. The seeder is exempt.

- **Message**: {image} is built by {a} and {b}. Fix: name them {unit}-{app}.
  {service}: the image {image} is outside {prefix}. Fix: name it {prefix}/{unit}-{app}.
- **Why**: Images are published under one prefix, and one name belongs to one unit; otherwise the last item with a name silently wins and an image could shadow another.

### IM03

*Error.* One image name, two build contexts or two Dockerfiles. The seeder is exempt.

- **Message**: {image} is built from {a} and from {b}. Fix: one name, one build.
- **Why**: One image name built from two places would be two different images with one name.

### IM04

*Error.* Images is not ghcr.io/&lt;owner>/&lt;repo> of this repository (in CI, or with --online).

- **Message**: catalog.yml images is {x}; this repository publishes to {y}. Fix: set images: {y}.
- **Why**: A catalog publishes to the registry path of its own repository, so that nothing it builds is pushed under another name.

## Build stage

### BD01

*Error.* Build stage of `catalog.py ci`.

- **Message**: {image}: the build failed at step {n}/{m}, {instruction} (exit {code}). Fix: run make ci to see it on your machine; the last 15 lines are in the summary.

### BD02

*Error.* Build stage of `catalog.py ci`.

- **Message**: {service}: build.{key} is not accepted. Fix: use context, dockerfile, args, target and labels only.

### BD04

*Error.* Build stage of `catalog.py ci`.

- **Message**: {image}: the build took over 10 minutes and was stopped.

### BD05

*Error.* Build stage of `catalog.py ci`.

- **Message**: Docker Hub refused the pull (rate limit). This is not your change: run the job again in a few minutes.

### BD06

*Error.* Plan stage of `catalog.py ci`.

- **Message**: Split this pull request: it changes {n} apps, the limit is 20.

## Manifests stage

### MF01

*Error.* Manifests stage of `catalog.py ci`.

- **Message**: {image}: the registry has no tag {tag}. Fix: use a tag that exists, or an earlier version.

### MF02

*Warning.* Manifests stage of `catalog.py ci`.

- **Message**: {registry} did not answer: {image} was not checked.

## Test stage

### TS01

*Error.* Test stage of `catalog.py ci`.

- **Message**: {app}: {n} tests failed. Fix: run make ci APP={app} to see them.

### TS02

*Warning.* Test stage of `catalog.py ci`.

- **Message**: {app} has no tests. Fix: COPY tests ./tests in the Dockerfile and add some.

## Needs stage

### ND01

*Error.* Needs stage of `catalog.py ci`.

- **Message**: {ids}: this catalog needs these catalogs and the secret NEEDS_DEPLOY_KEY is not set. Fix: give the repository a read-only deploy key of theirs: make settings prints the commands.

### ND02

*Error.* Needs stage of `catalog.py ci`.

- **Message**: {name} ({repository}) could not be fetched or linked: {why}. Fix: NEEDS_DEPLOY_KEY must be a deploy key of that repository, and ref a commit of it.

## Smoke stage

### SM01

*Error.* Smoke stage of `catalog.py ci`.

- **Message**: {service} was not healthy after 240 s

### SM02

*Error.* Smoke stage of `catalog.py ci`.

- **Message**: {service} exited with {code}

### SM03

*Warning.* Smoke stage of `catalog.py ci`.

- **Message**: {unit} was not tried: it needs settings or a license.

## Publish gate

### PB01

*Error.* Publish gate stage of `catalog.py ci`.

- **Message**: The version in catalog.yml ({a}) is not the tag ({b}). Fix: bump the version, then tag again.

### PB02

*Error.* Publish gate stage of `catalog.py ci`.

- **Message**: The tag {tag} is not on main.

### PB03

*Error.* Publish gate stage of `catalog.py ci`.

- **Message**: Catalog CI result has not passed on {sha7}. Fix: wait for it, or fix what failed.

### PB04

*Error.* Publish stage of `catalog.py ci`.

- **Message**: {image}:{version} was published from another commit. Fix: bump the version.

### PB05

*Error.* Publish gate stage of `catalog.py ci`.

- **Message**: catalog.yml images is {x}; this repository publishes to {y}.

### PB06

*Error.* Publish stage of `catalog.py ci`.

- **Message**: {image} carries a license label but is built from another project's source.

### PB07

*Error.* Guard stage of `catalog.py ci`.

- **Message**: main was force-pushed, it was at {sha}: the commits people pinned are gone. Fix: restore it from that commit, then run catalog.py repo-settings.

### PB08

*Error.* Publish gate stage of `catalog.py ci`.

- **Message**: The commit {sha7} carries a tool credit in its message or its author. main is never rewritten. Fix: find how it got there (the merge settings, V18) and write the next title without it.

## End-to-end plan

### E2E01

*Error.* E2e plan stage of `catalog.py ci`.

- **Message**: {item}: {service} reads GRAVITEE_LICENSE. Fix: only an overlay on a core service may read it.

### E2E02

*Error.* E2e plan stage of `catalog.py ci`.

- **Message**: A pull request is tried from the workflow of main. Fix: run this workflow on main.

### E2E03

*Error.* E2e plan and gate stage of `catalog.py ci`.

- **Message**: The head of pull request {n} is {sha7}, not the commit you read ({sha7}). Fix: read the new commits, then run again with the new one.

### E2E04

*Error.* E2e plan stage of `catalog.py ci`.

- **Message**: {input} is not what it says. Fix: pr is digits only; sha is 40 hexadecimal characters; items are words that name an item and do not start with a dash.

### E2E05

*Error.* E2e plan stage of `catalog.py ci`.

- **Message**: The change touches {paths}, which this workflow does not try. Fix: merge it first, a maintainer reads it as code, then try the items.

## Links stage

### LK01

*Warning.* Nightly stage of `catalog.py ci`.

- **Message**: {path}:{line}: the link {url} answers {status}.

## Scan stage

### SC01

*Error.* Scan stage of `catalog.py ci`.

- **Message**: {image}: {id} in {package} {version}, fixed in {fixed}. Fix: update the package or the base image.

### SC02

*Warning.* Scan stage of `catalog.py ci`.

- **Message**: {image}: {n} more flaws that fail nothing: {which}. Fix: update the base image or the packages once a fix is out.

## The Lab's trust policy

### T01

*Error.* The Lab's trust policy, class P.

- **Message**: Uses a Compose feature the Lab does not check: {key}. Fix: Remove `{key}`, or ask the Lab's maintainers to support it.
- **Why**: The Lab will not run an item that breaks this rule without the person agreeing to what it takes of their machine.

### T02

*Error.* The Lab's trust policy, class P.

- **Message**: Uses a Compose feature the Lab does not check: {key}. Fix: Remove `{key}`, or ask the Lab's maintainers to support it.
- **Why**: The Lab will not run an item that breaks this rule without the person agreeing to what it takes of their machine.

### T03

*Error.* The Lab's trust policy, class H.

- **Message**: Runs with full privileges. Fix: Remove `privileged`: a demo that needs it is a personal item.
- **Why**: The Lab will not run an item that breaks this rule without the person agreeing to what it takes of their machine.

### T04

*Error.* The Lab's trust policy, class H.

- **Message**: Reaches Docker from inside a container. Fix: Remove `use_api_socket`.
- **Why**: The Lab will not run an item that breaks this rule without the person agreeing to what it takes of their machine.

### T05

*Error.* The Lab's trust policy, class H.

- **Message**: Runs a program on this machine. Fix: Remove `provider`; use a container.
- **Why**: The Lab will not run an item that breaks this rule without the person agreeing to what it takes of their machine.

### T06

*Error.* The Lab's trust policy, class H.

- **Message**: Uses the host network (network_mode). Fix: Publish a port on 127.0.0.1 instead of sharing the host's network, processes or memory.
- **Why**: The Lab will not run an item that breaks this rule without the person agreeing to what it takes of their machine.

### T07

*Error.* The Lab's trust policy, class H.

- **Message**: Adds Linux capabilities. Fix: Remove `cap_add`; run the image as it is meant to run.
- **Why**: The Lab will not run an item that breaks this rule without the person agreeing to what it takes of their machine.

### T08

*Error.* The Lab's trust policy, class H.

- **Message**: Uses devices of this machine. Fix: Remove `devices`, `gpus`, `runtime` and device reservations.
- **Why**: The Lab will not run an item that breaks this rule without the person agreeing to what it takes of their machine.

### T09

*Error.* The Lab's trust policy, class H.

- **Message**: Lowers container isolation. Fix: Keep only `no-new-privileges` in `security_opt`; remove `sysctls`.
- **Why**: The Lab will not run an item that breaks this rule without the person agreeing to what it takes of their machine.

### T10

*Error.* The Lab's trust policy, class H.

- **Message**: Mounts the Docker socket. Fix: Remove the mount of the Docker socket.
- **Why**: The Lab will not run an item that breaks this rule without the person agreeing to what it takes of their machine.

### T11

*Error.* The Lab's trust policy, class H.

- **Message**: Mounts files outside its folder. Fix: Move the files under the unit's folder, or use a named volume.
- **Why**: The Lab will not run an item that breaks this rule without the person agreeing to what it takes of their machine.

### T12

*Error.* The Lab's trust policy, class P.

- **Message**: Writes to its own folder. Fix: Mount it `:ro`, or put the data under `.state/`.
- **Why**: The Lab will not run an item that breaks this rule without the person agreeing to what it takes of their machine.

### T13

*Error.* The Lab's trust policy, class P.

- **Message**: Mounts a volume that is not its own. Fix: Use a named volume the unit declares itself.
- **Why**: The Lab will not run an item that breaks this rule without the person agreeing to what it takes of their machine.

### T14

*Error.* The Lab's trust policy, class P.

- **Message**: Mounts a volume that is not its own. Fix: Use a named volume the unit declares itself.
- **Why**: The Lab will not run an item that breaks this rule without the person agreeing to what it takes of their machine.

### T15

*Error.* The Lab's trust policy, class P.

- **Message**: Joins a network that is not the Lab's. Fix: Attach the service to `internal` or `edge` only.
- **Why**: The Lab will not run an item that breaks this rule without the person agreeing to what it takes of their machine.

### T16

*Error.* The Lab's trust policy, class P.

- **Message**: Reads files outside its folder. Fix: Point `file:` at a file inside the unit.
- **Why**: The Lab will not run an item that breaks this rule without the person agreeing to what it takes of their machine.

### T17

*Error.* The Lab's trust policy, class P.

- **Message**: Publishes a port to the network. Fix: Write the port as `127.0.0.1:HOST:CONTAINER`.
- **Why**: The Lab will not run an item that breaks this rule without the person agreeing to what it takes of their machine.

### T18

*Error.* The Lab's trust policy, class P.

- **Message**: Builds from outside its folder. Fix: Put the build context under the unit, pin a remote context to a 40-character commit after `#`, and remove ssh, secrets and cache options. Only `context`, `dockerfile`, `args`, `target` and `labels` are accepted (default-deny).
- **Why**: The Lab will not run an item that breaks this rule without the person agreeing to what it takes of their machine.

### T19

*Error.* The Lab's trust policy, class P.

- **Message**: Includes content from outside the catalog. Fix: Use a relative path inside the catalog.
- **Why**: The Lab will not run an item that breaks this rule without the person agreeing to what it takes of their machine.

### T20

*Error.* The Lab's trust policy, class P.

- **Message**: Reads settings that are not its own: {names}. Fix: Prefix the setting with `{NS}_`, or read only `*_VERSION`, `*_REGISTRY`, `*_PULL_POLICY` and the shared settings.
- **Why**: The Lab will not run an item that breaks this rule without the person agreeing to what it takes of their machine.

### T21

*Error.* The Lab's trust policy, class P.

- **Message**: Same name as {owner}. Fix: Prefix service, container and volume names with the catalog id.
- **Why**: The Lab will not run an item that breaks this rule without the person agreeing to what it takes of their machine.

### T22

*Error.* The Lab's trust policy, class P.

- **Message**: Changes {label} beyond adding settings. Fix: On a platform service, set only `environment`, `extra_hosts`, `depends_on`, `labels`, read-only binds from your folder and loopback ports.
- **Why**: The Lab will not run an item that breaks this rule without the person agreeing to what it takes of their machine.

### T23

*Error.* The Lab's trust policy, class P.

- **Message**: Includes content from outside the catalog. Fix: Include only units of the default catalog, of this catalog, or of catalogs declared in `requires.catalogs`.
- **Why**: The Lab will not run an item that breaks this rule without the person agreeing to what it takes of their machine.

### T24

*Error.* The Lab's trust policy, class P.

- **Message**: Depends on private content. Fix: A Public catalog cannot include a Private one: move the shared part to a Public catalog.
- **Why**: The Lab will not run an item that breaks this rule without the person agreeing to what it takes of their machine.

### T25

*Error.* The Lab's trust policy, class P.

- **Message**: Needs {name}, which is blocked. Fix: Fix {name} first.
- **Why**: The Lab will not run an item that breaks this rule without the person agreeing to what it takes of their machine.

### T26

*Error.* The Lab's trust policy, class P.

- **Message**: Needs a newer Gravitee Lab. Fix: Remove what this Gravitee Lab does not read, or check with the Gravitee Lab release that reads it (`catalog.py upgrade --lab-ref latest`). A `lab` is for older Gravitee Labs: `catalog.py upgrade --write` writes the one of `catalog.yml`.
- **Why**: The Lab will not run an item that breaks this rule without the person agreeing to what it takes of their machine.

### T27

*Error.* The Lab's trust policy, class P.

- **Message**: Cannot be read: {last}. Fix: Run `docker compose -f {file} config` and fix what it prints.
- **Why**: The Lab will not run an item that breaks this rule without the person agreeing to what it takes of their machine.

### T28

*Error.* The Lab's trust policy, class P.

- **Message**: Uses this machine without saying why: {service}. Fix: Say why in `x-gravitee.host_access`, one reason for each service that uses this machine, or remove what it takes.
- **Why**: The Lab will not run an item that breaks this rule without the person agreeing to what it takes of their machine.

### T29

*Error.* The Lab's trust policy, class P.

- **Message**: Says it is a copy of an item of this Lab, which only this Lab writes. Fix: Remove `x-gravitee.copy_of`: only the Lab writes it, in the person's own copy of an item.
- **Why**: The Lab will not run an item that breaks this rule without the person agreeing to what it takes of their machine.

## The notes of the trust policy

### N01

*Info.* The Lab's trust policy, class N.

- **Message**: Brings its own seed code. Fix: Run the Lab's seeder as it is: build its folder with no other key, set no entrypoint or command, and mount only `/seed` and `/state`.

### N02

*Info.* The Lab's trust policy, class N.

- **Message**: Seed data reads a token. Fix: Do not read `APIM_TOKEN` or a secret in `seed/`: the Lab hands the seeder what it needs.

### N03

*Info.* The Lab's trust policy, class N.

- **Message**: Some of its addresses are not on this machine, so they are not opened. Fix: Point `io.gravitee.console.*.url` and `requires.host[].check` at localhost, 127.0.0.1, [::1] or host.docker.internal.

### N04

*Info.* The Lab's trust policy, class N.

- **Message**: Its gateway is not used: the Cloud pages do not offer it. Fix: Remove `x-gravitee.gateway`: only a catalog the Lab lists, or one a person allowed to use their machine, may declare a gateway.

### N05

*Info.* The Lab's trust policy, class N.

- **Message**: Its terminal is not offered by this catalog. Fix: Name one of the unit's own services, and not its seed, in `x-gravitee.terminal`.
