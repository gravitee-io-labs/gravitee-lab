"""`catalog.py explain CODE`: the long form of a rule code. It says what the rule is, the message CI prints, why the rule
exists, the fix in full and what the Lab does with the same file when a person adds the catalog.

The table of codes is built from the rule modules (`rules.structure.RULES` and the others, `workflows.RULES` and the tables of
`standard` and `codeowners`), the Lab's trust policy (T and N, with `trust.FIXES` when it has
them) and the codes of the CI stages, which are kept here. A test keeps every code of a module covered by a long form,
and `docs/catalog-rules.md` equals this table. Pure: it reads tables, runs nothing, writes nothing."""

import importlib
from typing import Dict, List, NamedTuple, Tuple

from .report import ERROR, WARNING, printable
from .rules import FAMILIES, Rule

SAME = "Not judged: the Lab does not install this file, or a person never sees it."
NOT_AT_ADD = "Not judged at Add: this is a rule about the repository, and the Lab judges the files it installs."
DROPPED = "The Lab does not install it and counts it as dropped, so a person would lose it without a word."


class Long(NamedTuple):
    why: str
    lab: str


# Why a rule exists and what the Lab does with the same file at Add, for the rules of this package
DETAILS: Dict[str, Long] = {
    "ST01": Long("A link can lead out of the catalog to a file of whoever reads the folder, and a tool that follows it reads "
                 "that file. A catalog is made of files.", DROPPED),
    "ST02": Long("A submodule is another repository that the catalog does not control and that a reader's git would fetch.",
                 DROPPED),
    "ST03": Long("A name with a character outside the safe set cannot be written the same on every system, and a `..` or a "
                 "backslash in a path is how a file is made to land outside its folder.", DROPPED),
    "ST04": Long("A path of more than 8 folders or 200 characters fails on some systems and is a sign of an archive bomb.",
                 DROPPED),
    "ST05": Long("An environment file holds what only one machine knows, often a secret. Removing the file from the tree does "
                 "not remove it from the history, so the secret has to be revoked.", DROPPED),
    "ST06": Long("A key, a certificate or the state of a machine does not belong in a repository that others clone. A demo "
                 "certificate is made by a script at start.", DROPPED),
    "ST07": Long("An agent instruction file inside an item would be read by the agent of whoever installs it and steer it. "
                 "They belong at the top of the catalog or in the kind folder, where the owners wrote them.", DROPPED),
    "ST08": Long("The Lab reads `catalog.yml` and the three folders of units; anything else stays in the repository. A path "
                 "elsewhere may be a mistake, so it is said once.", "Nothing: the file is not installed and not counted."),
    "ST09": Long("A catalog is read and installed whole, and a large file makes it slow for everyone.", DROPPED),
    "ST10": Long("A unit or a catalog over the limits (2000 files or 25 MB for a unit, 500 units, 10000 files or 100 MB for a "
                 "catalog) is too large to be read quickly and safely.",
                 "The unit's files are dropped, or the whole catalog is refused as too large."),
    "ST11": Long("An invisible or bidirectional character makes source read differently from how it runs (Trojan Source), and "
                 "a reviewer cannot see it.", SAME),
    "ST12": Long("Mixed line endings make a diff noisy and break scripts.", SAME),
    "ST13": Long("Two paths that differ only by case cannot both be written on a Mac or on Windows, so the second would "
                 "overwrite the first.", "The later of the two is dropped and counted, so that an install never fails on it."),
    "ST14": Long("An item is found by the name of its folder, and the names of items are lowercase letters, digits and dashes.",
                 DROPPED),
    "ST15": Long("An item is its `compose.yml` and what it mounts; a folder without one is not an item, and a file loose in a "
                 "kind folder belongs to none.", "The files are dropped: a unit is complete or absent."),
    "ST16": Long("A program or an archive in a catalog cannot be read by a reviewer. A unit is source, built in a Dockerfile.",
                 "Installed as data; the Lab never runs it."),
    "ST17": Long("Every catalog has the same files, so that contributors, reviewers and the tools find them in one place.",
                 NOT_AT_ADD),
    "ST18": Long("The license says what a contributor publishes under and what a person may do with the catalog.", NOT_AT_ADD),
    "ST19": Long("Git LFS stores a pointer in the repository and the file elsewhere, which the Lab does not fetch.",
                 "The pointer is installed as it is, not the file it stands for."),
    "ST20": Long("A seed is data. Code in it runs with the Lab's seeder, so the Lab treats the item as one that brings its "
                 "own seed code.", "The item says \"Brings its own seed code\" and is held back from a Cloud Token."),
    "ST21": Long("A pull request must not choose what the scan that judges it may skip. The scan uses the Lab's own "
                 "configuration, and a maintainer decides about exceptions.", NOT_AT_ADD),
    "ST22": Long("An editor task, a hook or a development container runs on the machine of whoever opens the folder, so a "
                 "pull request could run code on a reviewer's machine.", "Not installed (or installed as data), and never run."),
    "SE01": Long("A private key in a public repository is a leaked key. It is public in the pull request as soon as it is "
                 "pushed, even if a later commit removes it.", "Not judged: the Lab cannot know a secret."),
    "SE02": Long("These token shapes are what the services they belong to hand out. One in a repository is public.",
                 "Not judged: the Lab cannot know a secret."),
    "SE03": Long("Gitleaks is the broad net over the commits of the change, run with the Lab's own configuration.",
                 "Not judged: the Lab cannot know a secret."),
    "SE04": Long("A password, secret, key or token written in a Compose file is edited into a shared file by whoever changes "
                 "it. A setting with a default is the same for a demo and can be changed in `.env`.",
                 "Not judged: the Lab cannot know a secret."),
    "SE05": Long("A password in a URL is a password in the file. In a Compose file or a seed it is used, in a guide it is "
                 "usually an example.", "Not judged: the Lab cannot know a secret."),
    "SE06": Long("A catalog shared to a Public catalog must not name a private one: its registry, its image prefix or its "
                 "address.", "Not applicable: Share only."),
    "SE07": Long("An allow marker in the catalog would silence the scan in the run that judges it.", NOT_AT_ADD),
    "CR01": Long("A squash merge copies names into the message of `main`. A tool's name as an author, a committer or a "
                 "co-author would travel from a branch into `main`.", NOT_AT_ADD),
    "CR02": Long("A credit line in a message is noise in a history people pin. The squash drops it from `main`.", NOT_AT_ADD),
    "GD01": Long("A broken link in a guide is a broken guide, and a link that leaves the catalog leads nowhere once it is "
                 "installed.", "Not judged: a guide is sanitized when it is shown."),
    "GD02": Long("A `javascript:`, `data:` or `vbscript:` link runs code in the origin that shows the guide.",
                 "Not judged: the guide is sanitized and the page has a Content-Security-Policy."),
    "GD03": Long("A guide is text and diagrams. Raw HTML that runs (a script, a frame, a form, an event handler) is code in "
                 "the Lab's origin, which can drive Docker.",
                 "Not judged: the guide is sanitized and the page has a Content-Security-Policy."),
    "GD04": Long("An image that is not relative or https is fetched from somewhere a reader did not choose, or carries its "
                 "own content.", "Not judged: the guide is sanitized and the page has a Content-Security-Policy."),
    "GD05": Long("An SVG is a document that can run a script. The Lab shows it as a plain image, but an SVG with active "
                 "content is a mistake or an attack.", "The Lab serves it as data, never as a page of its own origin."),
    "DF01": Long("A build frontend is a program that runs in the build with its inputs.", "Not judged: hygiene, not power."),
    "DF02": Long("A file or a repository fetched at build time with no checksum changes without a commit.",
                 "Not judged: hygiene, not power."),
    "DF03": Long("A download that is run as it arrives is whatever the server sends that day.",
                 "Not judged: hygiene, not power."),
    "DF04": Long("A build is given no key and no host access: with them a build step could read the builder's secrets or "
                 "reach its machine.", "Not judged: hygiene, not power."),
    "DF05": Long("A secret written in the image is in every layer and every copy of it.", "Not judged: hygiene, not power."),
    "DF21": Long("A base image with no version changes under the catalog without a commit.", "Not judged: hygiene, not power."),
    "DF22": Long("A digest makes a base image exactly one image; Dependabot keeps `name:tag@sha256:...` current.",
                 "Not judged: hygiene, not power."),
    "DF23": Long("An app that runs as root has more power over its container than it needs.", "Not judged: hygiene, not power."),
    "DF24": Long("Files copied from another image are part of the image, and a reviewer reads them with the base images.",
                 "Not judged: hygiene, not power."),
    "IM01": Long("`pull_policy: ${ID_PULL_POLICY:-build}` lets a person choose to pull the published image or build it here; "
                 "without it Compose pulls or builds by its own rule.", "Not judged: hygiene."),
    "IM02": Long("Images are published under one prefix, and one name belongs to one unit; otherwise the last item with a name "
                 "silently wins and an image could shadow another.", "Not judged: hygiene."),
    "IM03": Long("One image name built from two places would be two different images with one name.", "Not judged: hygiene."),
    "IM04": Long("A catalog publishes to the registry path of its own repository, so that nothing it builds is pushed under "
                 "another name.", NOT_AT_ADD),
}


class Entry(NamedTuple):
    code: str
    severity: str
    rule: str
    message: str
    more: Tuple[str, ...] = ()
    why: str = ""
    lab: str = ""


# The rows of the tables of the stages and of the managed files, which no rule module holds
SD = (
    ("SD01", ERROR, "A managed file is missing", "{path} is missing from the shared files. Fix: catalog.py upgrade --write."),
    ("SD02", ERROR, "A managed file differs from the Lab's render at the repository's own level",
     "{path} was edited by hand. Fix: git checkout -- {path}. A change to a shared file is read by a maintainer."),
    ("SD03", WARNING, "A managed file is behind: the repository's level is below the Lab's (an error when that level is urgent)",
     "{path} is from standard {have}; this Lab has {now}. Fix: catalog.py upgrade --write."),
    ("SD04", ERROR, "standard.json is unreadable, has a bad value, or is over 4 KB (a warning for an unknown key)",
     ".github/standard.json: {problem}."),
    ("SD05", WARNING, "The repository's level is above the Lab's",
     ".github/standard.json says standard {n}; this Lab knows {m}. Fix: update the Lab with git pull, then run again."),
    ("SD06", ERROR, "The level went down, or a managed file lost its marker in this change (--base)",
     "{what}. Fix: undo that change; the level only goes up and a managed file keeps its marker."),
    ("SD07", ERROR, "The Lab could not be fetched (CI)",
     "The Lab could not be fetched. This is the repository's settings, not your change: a maintainer will run it again. Fix "
     "(maintainers): while the Lab is private this repository needs the secret LAB_DEPLOY_KEY (also as a Dependabot secret), "
     "and .github/standard.json must name a commit that exists."),
    ("SD08", "info", "A managed path has no marker: the catalog owns it",
     "{path} is owned by this catalog; upgrade skips it. WF01 to WF21 still apply."),
    ("SD09", WARNING, "The catalog owns dependabot.yml and it lacks github-actions or a cooldown",
     ".github/dependabot.yml has no {ecosystem}. Fix: add it."),
    ("SD10", ERROR, "The Makefile does not include .catalog/standard.mk",
     "Makefile does not include .catalog/standard.mk. Fix: add the line: include .catalog/standard.mk"),
    ("SD11", ERROR, "lab.ref is not the commit of a release of the Lab (a tag vX.Y.Z on its main) (CI)",
     "The Lab commit {sha7} is not a release of the Lab. Fix: pin the latest release (catalog.py upgrade --lab-ref latest)."),
    ("CO01", ERROR, "The file lacks the line * or the line /.github/, a line names no owner, or an owner is not @user or "
                    "@organization/team",
     ".github/CODEOWNERS: {path} has no owner. Fix: add the line: {path} {maintainers}"),
)
STAGES = (
    ("BD01", ERROR, "build", "{image}: the build failed at step {n}/{m}, {instruction} (exit {code}). Fix: run make ci to see it on "
                             "your machine; the last 15 lines are in the summary."),
    ("BD02", ERROR, "build", "{service}: build.{key} is not accepted. Fix: use context, dockerfile, args, target and labels only."),
    ("BD04", ERROR, "build", "{image}: the build took over 10 minutes and was stopped."),
    ("BD05", ERROR, "build", "Docker Hub refused the pull (rate limit). This is not your change: run the job again in a few "
                             "minutes."),
    ("BD06", ERROR, "plan", "Split this pull request: it changes {n} apps, the limit is 20."),
    ("MF01", ERROR, "manifests", "{image}: the registry has no tag {tag}. Fix: use a tag that exists, or an earlier version."),
    ("MF02", WARNING, "manifests", "{registry} did not answer: {image} was not checked."),
    ("TS01", ERROR, "test", "{app}: {n} tests failed. Fix: run make ci APP={app} to see them."),
    ("TS02", WARNING, "test", "{app} has no tests. Fix: COPY tests ./tests in the Dockerfile and add some."),
    ("ND01", ERROR, "needs", "{ids}: this catalog needs these catalogs and the secret NEEDS_DEPLOY_KEY is not set. Fix: give the repository a "
                             "read-only deploy key of theirs: make settings prints the commands."),
    ("ND02", ERROR, "needs", "{name} ({repository}) could not be fetched or linked: {why}. Fix: NEEDS_DEPLOY_KEY must be a deploy key of that "
                             "repository, and ref a commit of it."),
    ("SM01", ERROR, "smoke", "{service} was not healthy after 240 s"),
    ("SM02", ERROR, "smoke", "{service} exited with {code}"),
    ("SM03", WARNING, "smoke", "{unit} was not tried: it needs settings or a license."),
    ("PB01", ERROR, "publish gate", "The version in catalog.yml ({a}) is not the tag ({b}). Fix: bump the version, then tag again."),
    ("PB02", ERROR, "publish gate", "The tag {tag} is not on main."),
    ("PB03", ERROR, "publish gate", "Catalog CI result has not passed on {sha7}. Fix: wait for it, or fix what failed."),
    ("PB04", ERROR, "publish", "{image}:{version} was published from another commit. Fix: bump the version."),
    ("PB05", ERROR, "publish gate", "catalog.yml images is {x}; this repository publishes to {y}."),
    ("PB06", ERROR, "publish", "{image} carries a license label but is built from another project's source."),
    ("PB07", ERROR, "guard", "main was force-pushed, it was at {sha}: the commits people pinned are gone. Fix: restore it from "
                             "that commit, then run catalog.py repo-settings."),
    ("PB08", ERROR, "publish gate", "The commit {sha7} carries a tool credit in its message or its author. main is never "
                                    "rewritten. Fix: find how it got there (the merge settings, V18) and write the next title "
                                    "without it."),
    ("E2E01", ERROR, "e2e plan", "{item}: {service} reads GRAVITEE_LICENSE. Fix: only an overlay on a core service may read it."),
    ("E2E02", ERROR, "e2e plan", "A pull request is tried from the workflow of main. Fix: run this workflow on main."),
    ("E2E03", ERROR, "e2e plan and gate", "The head of pull request {n} is {sha7}, not the commit you read ({sha7}). Fix: read the "
                                          "new commits, then run again with the new one."),
    ("E2E04", ERROR, "e2e plan", "{input} is not what it says. Fix: pr is digits only; sha is 40 hexadecimal characters; items "
                                 "are words that name an item and do not start with a dash."),
    ("E2E05", ERROR, "e2e plan", "The change touches {paths}, which this workflow does not try. Fix: merge it first, a maintainer "
                                 "reads it as code, then try the items."),
    ("LK01", WARNING, "nightly", "{path}:{line}: the link {url} answers {status}."),
    ("SC01", ERROR, "scan", "{image}: {id} in {package} {version}, fixed in {fixed}. Fix: update the package or the base image."),
    ("SC02", WARNING, "scan", "{image}: {n} more flaws that fail nothing: {which}. Fix: update the base image or the packages once a "
                              "fix is out."),
)
T_FIXES = {
    "T01": "Remove `{key}`, or ask the Lab's maintainers to support it.", "T02": "Remove `{key}`, or ask the Lab's maintainers "
    "to support it.", "T03": "Remove `privileged`: a demo that needs it is a personal item.", "T04": "Remove `use_api_socket`.",
    "T05": "Remove `provider`; use a container.", "T06": "Publish a port on 127.0.0.1 instead of sharing the host's network, "
    "processes or memory.", "T07": "Remove `cap_add`; run the image as it is meant to run.",
    "T08": "Remove `devices`, `gpus`, `runtime` and device reservations.", "T09": "Keep only `no-new-privileges` in "
    "`security_opt`; remove `sysctls`.", "T10": "Remove the mount of the Docker socket.", "T11": "Move the files under the "
    "unit's folder, or use a named volume.", "T12": "Mount it `:ro`, or put the data under `.state/`.",
    "T13": "Use a named volume the unit declares itself.", "T14": "Use a named volume the unit declares itself.",
    "T15": "Attach the service to `internal` or `edge` only.", "T16": "Point `file:` at a file inside the unit.",
    "T17": "Write the port as `127.0.0.1:HOST:CONTAINER`.", "T18": "Put the build context under the unit, pin a remote context to "
    "a 40-character commit after `#`, and remove ssh, secrets and cache options. Only `context`, `dockerfile`, `args`, `target` "
    "and `labels` are accepted (default-deny; `additional_contexts` only for the Lab's seeder).",
    "T19": "Use a relative path inside the catalog.", "T20": "Prefix the setting with `{NS}_`, or read only `*_VERSION`, "
    "`*_REGISTRY`, `*_PULL_POLICY` and the shared settings.", "T21": "Prefix service, container and volume names with the "
    "catalog id.", "T22": "On a platform service, set only `environment`, `extra_hosts`, `depends_on`, `labels`, read-only "
    "binds from your folder and loopback ports.", "T23": "Include only units of the default catalog, of this catalog, or of "
    "catalogs declared in `requires.catalogs`.", "T24": "A Public catalog cannot include a Private one: move the shared part "
    "to a Public catalog.", "T25": "Fix {name} first.", "T26": "Remove what this Gravitee Lab does not read, or "
    "check with the Gravitee Lab release that reads it (`catalog.py upgrade --lab-ref latest`). A `lab` is for older Gravitee "
    "Labs: `catalog.py upgrade --write` writes the one of `catalog.yml`.", "T27": "Run `docker compose -f {file} config` and fix what it "
    "prints.",
}


def _module_rules(name: str) -> Dict[str, Rule]:
    """The RULES table of a rule module of this package."""
    module = importlib.import_module(f"{__package__}.{name}")
    return {code: rule for code, rule in getattr(module, "RULES", {}).items() if isinstance(rule, tuple) and len(rule) >= 4}


def _more(name: str) -> Dict[str, Tuple[str, ...]]:
    return getattr(importlib.import_module(f"{__package__}.{name}"), "MORE", {})


def table() -> Dict[str, Entry]:
    """Every code this Lab knows, with what to say about it, from the rule modules, the trust policy and the tables above."""
    found: Dict[str, Entry] = {}
    for name in ("rules.structure", "rules.secrets", "rules.commits", "rules.guides", "rules.dockerfiles", "rules.images",
                 "rules.workflows", "rules.codeowners"):
        more = _more(name)
        for code, rule in _module_rules(name).items():
            long = DETAILS.get(code, Long("", ""))
            found[code] = Entry(code, rule[1], rule[2], rule[3], tuple(more.get(code, ())), long.why, long.lab)
    for code, severity, rule, message in SD:
        found.setdefault(code, Entry(code, severity, rule, message, (), "", NOT_AT_ADD))
    for code, severity, stage, message in STAGES:
        found[code] = Entry(code, severity, f"{stage} stage of `catalog.py ci`", message, (), "", NOT_AT_ADD)
    trust = importlib.import_module(f"{__package__}.trust")
    for code, row in (getattr(trust, "RULES", None) or {}).items():
        lines = row.get("lines") or ("",)
        fix = (getattr(trust, "FIXES", None) or T_FIXES).get(code, T_FIXES.get(code, ""))
        found[code] = Entry(code, ERROR, f"the Lab's trust policy, class {row.get('class', '')}".rstrip(", "),
                            f"{lines[0]}. Fix: {fix}" if fix else lines[0], (), "The Lab will not run an item that breaks this "
                            "rule without the person agreeing to what it takes of their machine.",
                            "Blocked at Add, Update and Start, with this reason.")
    return found


def codes() -> List[str]:
    """Every code explain knows, sorted by family and number."""
    return sorted(table(), key=lambda code: (_family_index(code), code))


def _family_index(code: str) -> int:
    letters = code.rstrip("0123456789")
    return FAMILIES.index(letters) if letters in FAMILIES else len(FAMILIES)


def explain(code: str) -> str:
    """The long form of a rule code, as text to print; for a code that is not one it says so and lists the families."""
    wanted = (code or "").strip().upper()
    entry = table().get(wanted)
    if entry is None:
        known = sorted({each.rstrip("0123456789") for each in table()})
        return f"{printable(code)} is not a rule code. The families are {', '.join(known)}; try make explain CODE=ST05.\n"
    lines = [f"{entry.code} ({entry.severity})", "", f"Rule     {entry.rule}", f"Message  {entry.message}"]
    lines += [f"         {more}" for more in entry.more]
    fix = entry.message.partition("Fix:")[2].strip()
    if entry.why:
        lines += ["", f"Why      {entry.why}"]
    if fix:
        lines += ["", f"Fix      {fix}"]
    if entry.lab:
        lines += ["", f"At Add   {entry.lab}"]
    return "\n".join(lines) + "\n"


FAMILY_NAMES = {
    "SD": "Managed files", "ST": "Structure", "SE": "Secrets", "CR": "Commits", "GD": "Guides", "DF": "Dockerfiles",
    "IM": "Images", "WF": "Workflows", "CO": "Code owners", "BD": "Build stage", "MF": "Manifests stage", "TS": "Test stage",
    "ND": "Needs stage", "SM": "Smoke stage", "PB": "Publish gate", "E2E": "End-to-end plan", "LK": "Links stage", "SC": "Scan stage",
    "T": "The Lab's trust policy", "N": "The notes of the trust policy",
}


def _text(words: str) -> str:
    """Words for a Markdown page: an angle bracket, `srcdoc=` and `data:text/html` are written with an entity, so that a rule that names
    them is not raw HTML in a guide (GD03 reads the page like any other)."""
    return words.replace("<", "&lt;").replace("srcdoc=", "srcdoc&#61;").replace("data:text/html", "data&#58;text/html")


def document() -> str:
    """`docs/catalog-rules.md`: every rule code with its long form, by family. A test keeps the file equal to this text, so that the
    page a failed check leads to says what `catalog.py explain` says."""
    entries = table()
    lines = ["# Reading a failed check", "",
             "Every rule that `catalog.py check` and the stages of `catalog.py ci` can print, with what it means and how to fix it. "
             "`make explain CODE=ST05` prints the same for one code. A finding is an error, which fails the check, or a warning, which "
             "says what to look at. The Lab judges the files it installs when a person adds a catalog: *At Add* says what it does with "
             "the same file.", ""]
    order = [family for family in (*FAMILIES, "BD", "MF", "TS", "ND", "SM", "PB", "E2E", "LK", "SC", "T", "N")]
    lines += ["## Families", "", "| Family | Codes |", "|---|---|"]
    for family in order:
        found = [code for code in codes() if code.rstrip("0123456789") == family]
        if found:
            lines.append(f"| [{FAMILY_NAMES.get(family, family)}](#{FAMILY_NAMES.get(family, family).lower().replace(' ', '-').replace(chr(39), '')}) "
                         f"| {'`' + found[0] + '`' + (' to `' + found[-1] + '`' if len(found) > 1 else '')} |")
    lines.append("")
    for family in order:
        found = [code for code in codes() if code.rstrip("0123456789") == family]
        if not found:
            continue
        lines += [f"## {FAMILY_NAMES.get(family, family)}", ""]
        for code in found:
            entry = entries[code]
            fix = entry.message.partition("Fix:")[2].strip()
            rule = entry.rule if entry.rule.endswith((".", ":", "?")) else entry.rule + "."
            lines += [f"### {code}", "", f"*{entry.severity.capitalize()}.* {_text(rule[0].upper() + rule[1:])}", "", f"- **Message**: {_text(entry.message)}"]
            lines += [f"  {_text(more)}" for more in entry.more]
            if entry.why:
                lines.append(f"- **Why**: {_text(entry.why)}")
            if fix:
                lines.append(f"- **Fix**: {_text(fix)}")
            if entry.lab:
                lines.append(f"- **At Add**: {_text(entry.lab)}")
            lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


__all__ = ["DETAILS", "FAMILY_NAMES", "T_FIXES", "codes", "document", "explain", "table"]
