"""`catalog.py explain CODE`: the long form of a rule code. It says what the rule is, the message CI prints with its fix and why
the rule exists.

The table of codes is built from the rule modules (`rules.structure.RULES` and the others, `workflows.RULES` and the tables of
`standard` and `codeowners`), the Lab's trust policy (T and N, with `trust.FIXES`) and the codes of the CI stages, which are
kept here. A test keeps every code of a module covered by a long form,
and `docs/catalog-rules.md` equals this table. Pure: it reads tables, runs nothing, writes nothing."""

from typing import Dict, List, NamedTuple, Tuple

from . import standard, trust
from .report import ERROR, INFO, WARNING, printable
from .rules import FAMILIES, codeowners, commits, dockerfiles, guides, images, secrets, structure, workflows

# Why a rule exists, for the rules of this package
DETAILS: Dict[str, str] = {
    "ST01": "A link can lead out of the catalog to a file of whoever reads the folder, and a tool that follows it reads "
            "that file. A catalog is made of files.",
    "ST02": "A submodule is another repository that the catalog does not control and that a reader's git would fetch.",
    "ST03": "A name with a character outside the safe set cannot be written the same on every system, and a `..` or a "
            "backslash in a path is how a file is made to land outside its folder.",
    "ST04": "A path of more than 8 folders or 200 characters fails on some systems and is a sign of an archive bomb.",
    "ST05": "An environment file holds what only one machine knows, often a secret. Removing the file from the tree does "
            "not remove it from the history, so the secret has to be revoked.",
    "ST06": "A key, a certificate or the state of a machine does not belong in a repository that others clone. A demo "
            "certificate is made by a script at start.",
    "ST07": "An agent instruction file inside an item would be read by the agent of whoever installs it and steer it. They "
            "belong at the top of the catalog or in the kind folder, where the owners wrote them.",
    "ST08": "The Lab reads `catalog.yml` and the three folders of units; anything else stays in the repository. A path "
            "elsewhere may be a mistake, so it is said once.",
    "ST09": "A catalog is read and installed whole, and a large file makes it slow for everyone.",
    "ST10": "A unit or a catalog over the limits (2000 files or 25 MB for a unit, 500 units, 10000 files or 100 MB for a "
            "catalog) is too large to be read quickly and safely.",
    "ST11": "An invisible or bidirectional character makes source read differently from how it runs (Trojan Source), and a "
            "reviewer cannot see it.",
    "ST12": "Mixed line endings make a diff noisy and break scripts.",
    "ST13": "Two paths that differ only by case cannot both be written on a Mac or on Windows, so the second would "
            "overwrite the first.",
    "ST14": "An item is found by the name of its folder, and the names of items are lowercase letters, digits and dashes. "
            "`local` would read as the items of this machine.",
    "ST15": "An item is its `compose.yml` and what it mounts; a folder without one is not an item, and a file loose in a "
            "kind folder belongs to none.",
    "ST16": "A program or an archive in a catalog cannot be read by a reviewer. A unit is source, built in a Dockerfile.",
    "ST17": "Every catalog has the same files, so that contributors, reviewers and the tools find them in one place.",
    "ST18": "The license says what a contributor publishes under and what a person may do with the catalog.",
    "ST19": "Git LFS stores a pointer in the repository and the file elsewhere, which the Lab does not fetch.",
    "ST20": "A seed is data. Code in it runs with the Lab's seeder, so the Lab treats the item as one that brings its own "
            "seed code.",
    "ST21": "A pull request must not choose what the scan that judges it may skip. The scan uses the Lab's own "
            "configuration, and a maintainer decides about exceptions.",
    "ST22": "An editor task, a hook or a development container runs on the machine of whoever opens the folder, so a pull "
            "request could run code on a reviewer's machine.",
    "ST23": "A person finds an item by its name, and the Lab finds it by its folder: its id, what other items include, the "
            "prefix of its settings, its stars and its records. One name for both is what a reader expects and what the "
            "Lab UI makes for a new item.",
    "SE01": "A private key in a public repository is a leaked key. It is public in the pull request as soon as it is "
            "pushed, even if a later commit removes it.",
    "SE02": "These token shapes are what the services they belong to hand out. One in a repository is public.",
    "SE03": "Gitleaks is the broad net over the commits of the change, run with the Lab's own configuration.",
    "SE04": "A password, secret, key or token written in a Compose file is edited into a shared file by whoever changes "
            "it. A setting with a default is the same for a demo and can be changed in `.env`.",
    "SE05": "A password in a URL is a password in the file. In a Compose file or a seed it is used, in a guide it is "
            "usually an example.",
    "SE07": "An allow marker in the catalog would silence the scan in the run that judges it.",
    "CR01": "A squash merge copies names into the message of `main`. A tool's name as an author, a committer or a "
            "co-author would travel from a branch into `main`.",
    "CR02": "A credit line in a message is noise in a history people pin. The squash drops it from `main`.",
    "GD01": "A broken link in a guide is a broken guide, and a link that leaves the catalog leads nowhere once it is "
            "installed.",
    "GD02": "A `javascript:`, `data:` or `vbscript:` link runs code in the origin that shows the guide.",
    "GD03": "A guide is text and diagrams. Raw HTML that runs (a script, a frame, a form, an event handler) is code in the "
            "Lab's origin, which can drive Docker.",
    "GD04": "An image that is not relative or https is fetched from somewhere a reader did not choose, or carries its own "
            "content.",
    "GD05": "An SVG is a document that can run a script. The Lab shows it as a plain image, but an SVG with active content "
            "is a mistake or an attack.",
    "DF01": "A build frontend is a program that runs in the build with its inputs.",
    "DF02": "A file or a repository fetched at build time with no checksum changes without a commit.",
    "DF03": "A download that is run as it arrives is whatever the server sends that day.",
    "DF04": "A build is given no key and no host access: with them a build step could read the builder's secrets or reach "
            "its machine.",
    "DF05": "A secret written in the image is in every layer and every copy of it.",
    "DF21": "A base image with no version changes under the catalog without a commit.",
    "DF22": "A digest makes a base image exactly one image; Dependabot keeps `name:tag@sha256:...` current.",
    "DF23": "An app that runs as root has more power over its container than it needs.",
    "DF24": "Files copied from another image are part of the image, and a reviewer reads them with the base images.",
    "IM01": "`pull_policy: ${ID_PULL_POLICY:-build}` lets a person choose to pull the published image or build it here; "
            "without it Compose pulls or builds by its own rule.",
    "IM02": "Images are published under one prefix, and one name belongs to one unit; otherwise the last item with a name "
            "silently wins and an image could shadow another.",
    "IM03": "One image name built from two places would be two different images with one name.",
    "IM04": "A catalog publishes to the registry path of its own repository, so that nothing it builds is pushed under "
            "another name.",
}


class Entry(NamedTuple):
    code: str
    severity: str
    rule: str
    message: str
    more: Tuple[str, ...] = ()
    why: str = ""


# The rows of the table of the stages, which no rule module holds
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


def table() -> Dict[str, Entry]:
    """Every code this Lab knows, with what to say about it, from the rule modules, the trust policy and the tables above."""
    found: Dict[str, Entry] = {}
    for module in (structure, secrets, commits, guides, dockerfiles, images, workflows, codeowners):
        more = getattr(module, "MORE", {})
        for code, rule in module.RULES.items():
            found[code] = Entry(code, rule[1], rule[2], rule[3], tuple(more.get(code, ())), DETAILS.get(code, ""))
    for code, rule in standard.RULES.items():
        found[code] = Entry(code, rule[1], rule[2], rule[3])
    for code, severity, stage, message in STAGES:
        found[code] = Entry(code, severity, f"{stage} stage of `catalog.py ci`", message)
    for code, row in trust.RULES.items():
        blocks = row["class"] != "N"
        found[code] = Entry(code, ERROR if blocks else INFO, f"the Lab's trust policy, class {row['class']}",
                            f"{row['lines'][0]}. Fix: {trust.FIXES[code]}", (),
                            "The Lab will not run an item that breaks this rule without the person agreeing to what it takes "
                            "of their machine." if blocks else "")
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
    if entry.why:
        lines += ["", f"Why      {entry.why}"]
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
             "says what to look at.", ""]
    order = (*FAMILIES, "BD", "MF", "TS", "ND", "SM", "PB", "E2E", "LK", "SC", "T", "N")
    lines += ["## Families", "", "| Family | Codes |", "|---|---|"]
    for family in order:
        found = [code for code in codes() if code.rstrip("0123456789") == family]
        lines.append(f"| [{FAMILY_NAMES[family]}](#{FAMILY_NAMES[family].lower().replace(' ', '-').replace(chr(39), '')}) "
                     f"| {'`' + found[0] + '`' + (' to `' + found[-1] + '`' if len(found) > 1 else '')} |")
    lines.append("")
    for family in order:
        found = [code for code in codes() if code.rstrip("0123456789") == family]
        lines += [f"## {FAMILY_NAMES[family]}", ""]
        for code in found:
            entry = entries[code]
            rule = entry.rule if entry.rule.endswith((".", ":", "?")) else entry.rule + "."
            lines += [f"### {code}", "", f"*{entry.severity.capitalize()}.* {_text(rule[0].upper() + rule[1:])}", "", f"- **Message**: {_text(entry.message)}"]
            lines += [f"  {_text(more)}" for more in entry.more]
            if entry.why:
                lines.append(f"- **Why**: {_text(entry.why)}")
            lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"
