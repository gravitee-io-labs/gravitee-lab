"""Catalogs/rules/workflows.py: the WF rules of the catalog repository standard. The managed files of the template are the passing set;
every rule has planted violations (a mutation test) and each violation is tried in five spellings of the same file (as it is, the
indentation doubled, sequences at the column of their key, Windows line ends, a byte order mark), because the rules read by
indentation and must not depend on how a file is laid out. Hostile text is the other half: a line of 1 MB and every kind of
unreadable file. The aggregator of `ci.yml` is run with bash on blank, empty, skipped and canceled answers (a visible skip
without bash). No network, no Docker."""

import ast
import os
import random
import re
import shutil
import subprocess
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from typing import NamedTuple

from lab_ui.catalogs.rules import workflows as wf

HERE = Path(__file__).resolve().parents[2]
MANAGED = HERE.parent / "templates" / "catalog" / "managed"
ROOT = HERE.parents[1]
CHECKOUT = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
PYTHON = "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97"
LOGIN = "docker/login-action@dbcb813823bdd20940b903addbd779551569679f"
SCRIPT = "actions/github-script@" + "a" * 40
DOWNLOAD = "actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333"

# The managed files, by the short name the tests use
FILES = {"ci": ".github/workflows/ci.yml", "publish": ".github/workflows/publish.yml", "nightly": ".github/workflows/nightly.yml",
         "e2e": ".github/workflows/e2e.yml", "versions": ".github/workflows/versions.yml", "lab": ".github/actions/lab/action.yml"}

def text_of(name):
    return (MANAGED / FILES[name]).read_text(encoding="utf-8")


def path_of(name):
    return FILES[name]


def codes(name, text, rules=wf.ALL):
    """The codes a text breaks, as the file called `name` (its place and its name decide what it may do)."""
    return sorted({found.code for found in wf.problems(path_of(name), text, rules)})


def line_of(text, fragment, after=0):
    """The number of the first line from line `after` that holds the fragment."""
    for number, row in enumerate(text.splitlines(), 1):
        if number > after and fragment in row:
            return number
    raise AssertionError("no line holds %r" % fragment)


def edit(text, old, new):
    """`text` with the first `old` replaced; a fixture that no longer holds `old` fails the test and not the rule."""
    assert old in text, "the fixture does not hold %r" % old
    return text.replace(old, new, 1)


def with_step(text, job, first, *rest):
    """A step added at the start of the job's steps. `first` follows the dash; `rest` are the other lines of the step, written
    from the column of the first key."""
    head = "\n  %s:\n" % job
    assert head in text, job
    start = text.index(head) + len(head)
    marker = "    steps:\n"
    at = text.index(marker, start) + len(marker)
    lines = ["      - " + first] + ["        " + line for line in rest]
    return text[:at] + "\n".join(lines) + "\n" + text[at:]


def in_job(text, job, *lines):
    """Keys added at the top of a job; the lines carry their own indentation from the column of the keys."""
    head = "\n  %s:\n" % job
    assert head in text, job
    at = text.index(head) + len(head)
    return text[:at] + "".join("    " + line + "\n" for line in lines) + text[at:]


# ---------------------------------------------------------------- the same file, laid out another way

def reindented(text, factor):
    """Every column `factor` times as far from the left, the dash of a list item and the key after it included (so a file
    written with two spaces is read with four, or with eight)."""
    def widen(found):
        return " " * (factor * len(found.group(1))) + ("-" + " " * (2 * factor - 1)) * (len(found.group(2)) // 2)
    return "".join(re.sub(r"^( *)((?:- )*)", widen, row) for row in text.splitlines(True))


def doubled(text):
    return reindented(text, 2)


def compact(text):
    """The sequences at the column of the key that holds them (`steps:` then `- uses:` at the same indentation)."""
    lines = text.split("\n")
    changed = True
    while changed:
        changed = False
        for i, row in enumerate(lines):
            found = re.match(r"( *)((?:- )?)[A-Za-z][\w-]*:\s*$", row)
            if not found:
                continue
            col = len(found.group(1)) + len(found.group(2))
            j = i + 1
            while j < len(lines) and (not lines[j].strip() or lines[j].lstrip().startswith("#")):
                j += 1
            if j >= len(lines) or not lines[j].startswith(" " * (col + 2) + "- "):
                continue
            end = j
            while end < len(lines) and (not lines[end].strip() or len(lines[end]) - len(lines[end].lstrip()) > col):
                end += 1
            for k in range(j, end):
                if lines[k].startswith("  "):
                    lines[k] = lines[k][2:]
            changed = True
            break
    return "\n".join(lines)


FORMS = {"as it is": lambda text: text, "indentation doubled": doubled, "sequences at the column of their key": compact,
         "Windows line ends": lambda text: text.replace("\n", "\r\n"), "a byte order mark": lambda text: "\ufeff" + text}


class Planted(NamedTuple):
    """A planted violation (or, with no code wanted, a near miss): the file it is planted in, how, and the codes that must fire."""
    label: str
    file: str
    mutate: object
    want: list
    forms: tuple = tuple(FORMS)


def step(file, label, want, first, *rest, job="static"):
    """A violation that is a step added to a job."""
    return Planted(label, file, lambda text: with_step(text, job, first, *rest), sorted(want))


def swap(file, label, want, old, new, forms=tuple(FORMS)):
    """A violation that is one piece of the file replaced."""
    return Planted(label, file, lambda text: edit(text, old, new), sorted(want), forms)


def job_key(file, label, want, job, *lines):
    return Planted(label, file, lambda text: in_job(text, job, *lines), sorted(want))


ON_CI = "on:\n  pull_request:\n  push:\n    branches: [main]\n  workflow_dispatch:\n"
TOP_PERMISSIONS = "permissions:\n  contents: read\n\nconcurrency:\n  group: ci-"
CONCURRENCY = ("concurrency:\n  group: ci-${{ github.event_name }}-${{ github.event_name == 'push' && github.sha || github.ref }}\n"
               "  cancel-in-progress: ${{ github.event_name == 'pull_request' }}\n\n")
RUNNER = "    runs-on: ubuntu-24.04\n    timeout-minutes: 15\n"
MATRIX = "      matrix: ${{ fromJSON(needs.static.outputs.matrix) }}\n"
IMAGES = "    environment: publish\n    permissions:\n      contents: read\n      packages: write\n    outputs:"
ATTEST = "    environment: publish\n    permissions:\n      contents: read\n      packages: write\n      id-token: write"
# A backslash is made here and no escape sequence is written in this file, so that nothing that reads source text can turn
# `<backslash>x24` into a `$` (that happened once to a table of the module itself)
BS = chr(92)


MUTATIONS = {
    "WF01": [
        swap("ci", "pull_request_target", ["WF01"], "  pull_request:\n  push:", "  pull_request_target:\n  push:"),
        swap("ci", "workflow_run", ["WF01"], "  workflow_dispatch:\n\npermissions:",
             "  workflow_dispatch:\n  workflow_run:\n    workflows: [CI]\n\npermissions:"),
        swap("ci", "an event no rule names", ["WF01"], "  workflow_dispatch:\n\npermissions:",
             "  workflow_dispatch:\n  issue_comment:\n\npermissions:"),
        swap("ci", "a scalar", ["WF01"], ON_CI, "on: pull_request_target\n"),
        swap("ci", "a block list", ["WF01"], ON_CI, "on:\n  - pull_request\n  - issue_comment\n"),
        swap("ci", "a flow list", ["WF21"], ON_CI, "on: [pull_request, pull_request_target]\n"),
        swap("ci", "a flow mapping", ["WF21"], ON_CI, "on: {pull_request_target: {}}\n"),
        swap("ci", "another letter case", ["WF01"], "  pull_request:\n  push:", "  Pull_Request:\n  push:"),
        swap("ci", "another letter case of on", ["WF01", "WF21"], ON_CI, "On:\n  pull_request_target:\n"),
        swap("nightly", "the nightly run on a pull request target", ["WF01"], "  workflow_dispatch:\n\npermissions:",
             "  workflow_dispatch:\n  pull_request_target:\n\npermissions:"),
        swap("publish", "the publish lane on a release event", ["WF01"], "  workflow_dispatch:\n\npermissions:",
             "  workflow_dispatch:\n  release:\n\npermissions:"),
    ],
    "WF02": [
        swap("ci", "a tag", ["WF02"], CHECKOUT + " # v7.0.1", "actions/checkout@v7"),
        step("ci", "a branch", ["WF02"], "uses: actions/setup-python@main"),
        step("ci", "39 characters", ["WF02"], "uses: " + PYTHON[:-1]),
        step("ci", "41 characters", ["WF02"], "uses: " + PYTHON + "a"),
        step("ci", "capital letters in the commit", ["WF02"], "uses: " + PYTHON.upper().replace("ACTIONS/SETUP-PYTHON", "actions/setup-python")),
        step("ci", "7 characters", ["WF02"], "uses: actions/setup-python@5fda3b9"),
        step("ci", "text after the commit", ["WF02"], "uses: " + PYTHON + "x"),
        step("ci", "an image", ["WF02"], "uses: docker://alpine:3.20"),
        step("ci", "a folder outside the repository", ["WF02"], "uses: ./../outside"),
        step("ci", "an expression in a folder", ["WF02"], "uses: ./${{ inputs.action }}"),
        step("ci", "a repository called ..", ["WF02"], "uses: actions/../checkout@" + "a" * 40),
        step("ci", "unpinned and quoted", ["WF02", "WF07"], 'uses: "actions/checkout@v4"'),
        job_key("ci", "a reusable workflow at a branch", ["WF02"], "static", "uses: o/r/.github/workflows/x.yml@main"),
        swap("ci", "an action in a list of one", ["WF02", "WF21"], "uses: " + CHECKOUT, "uses: [actions/checkout@v4]"),
    ],
    "WF03": [
        swap("ci", "missing", ["WF03"], "permissions:\n  contents: read\n\n", ""),
        swap("ci", "write", ["WF03"], TOP_PERMISSIONS, "permissions:\n  contents: write\n\nconcurrency:\n  group: ci-"),
        swap("ci", "one more", ["WF03"], TOP_PERMISSIONS, "permissions:\n  contents: read\n  packages: read\n\nconcurrency:\n  group: ci-"),
        swap("ci", "read-all", ["WF03", "WF21"], "permissions:\n  contents: read\n", "permissions: read-all\n"),
        swap("ci", "write-all", ["WF03", "WF21"], "permissions:\n  contents: read\n", "permissions: write-all\n"),
        swap("ci", "a flow mapping", ["WF03", "WF21"], "permissions:\n  contents: read\n", "permissions: {contents: read}\n"),
        swap("ci", "an empty flow mapping", ["WF03", "WF21"], "permissions:\n  contents: read\n", "permissions: {}\n"),
        swap("ci", "another letter case", ["WF03", "WF21"], "permissions:\n  contents: read\n", "Permissions:\n  contents: read\n"),
        swap("ci", "contents twice", ["WF03", "WF21"], "permissions:\n  contents: read\n",
             "permissions:\n  contents: read\n  contents: read\n"),
        swap("ci", "a quoted key", ["WF21"], "permissions:\n  contents: read\n", '"permissions":\n  contents: read\n'),
        swap("ci", "no value", ["WF03"], "permissions:\n  contents: read\n", "permissions:\n"),
    ],
    "WF04": [
        step("ci", "a title in a script", ["WF04"], 'run: echo "${{ github.event.pull_request.title }}"'),
        step("ci", "a block", ["WF04"], "run: |", "  echo one", "  echo ${{ github.head_ref }}"),
        step("ci", "the value on the next line", ["WF04"], "run:", "    echo ${{ github.head_ref }}"),
        step("ci", "a quoted script", ["WF04"], 'run: "echo ${{ github.head_ref }}"'),
        step("ci", "a single-quoted script", ["WF04"], "run: 'echo ${{ github.head_ref }}'"),
        step("ci", "a folded block", ["WF04"], "run: >-", "  echo ${{ github.head_ref }}"),
        step("ci", "another letter case", ["WF04", "WF21"], "Run: echo ${{ github.head_ref }}"),
        step("ci", "a script in a composite step with other keys", ["WF04"], "name: x", "shell: bash",
             "run: echo ${{ inputs.x }}"),
        swap("lab", "the same in an action", ["WF04"], "  steps:\n", "  steps:\n    - run: echo ${{ inputs.key }}\n"),
    ],
    "WF05": [
        swap("ci", "no timeout", ["WF05"], "    timeout-minutes: 15\n", ""),
        swap("ci", "not a number", ["WF05"], "timeout-minutes: 15", "timeout-minutes: soon"),
        swap("ci", "zero", ["WF05"], "timeout-minutes: 15", "timeout-minutes: 0"),
        swap("ci", "an expression", ["WF05"], "timeout-minutes: 15", "timeout-minutes: ${{ inputs.minutes }}"),
        swap("ci", "empty", ["WF05"], "timeout-minutes: 15", "timeout-minutes:"),
        swap("ci", "another letter case", ["WF05", "WF21"], "timeout-minutes: 15", "Timeout-Minutes: 15"),
        swap("publish", "the publish lane", ["WF05"], "    timeout-minutes: 90\n", ""),
    ],
    "WF06": [
        swap("ci", "latest", ["WF06"], RUNNER, "    runs-on: ubuntu-latest\n    timeout-minutes: 15\n"),
        swap("ci", "self-hosted", ["WF06"], RUNNER, "    runs-on: self-hosted\n    timeout-minutes: 15\n"),
        swap("ci", "a flow list", ["WF06", "WF21"], RUNNER, "    runs-on: [self-hosted, linux]\n    timeout-minutes: 15\n"),
        swap("ci", "a block list", ["WF06"], RUNNER, "    runs-on:\n      - ubuntu-24.04\n    timeout-minutes: 15\n"),
        swap("ci", "a group", ["WF06"], RUNNER, "    runs-on:\n      group: big\n    timeout-minutes: 15\n"),
        swap("ci", "an expression", ["WF06"], RUNNER, "    runs-on: ${{ inputs.runner }}\n    timeout-minutes: 15\n"),
        swap("ci", "a quoted label", ["WF06"], RUNNER, "    runs-on: 'ubuntu-22.04'\n    timeout-minutes: 15\n"),
        swap("ci", "a matrix that lists the labels", ["WF06"], MATRIX, "      matrix:\n        runner: [ubuntu-24.04, ubuntu-24.04-arm]\n"),
        swap("ci", "a matrix of a job that is not in the file", ["WF06"], MATRIX,
             "      matrix: ${{ fromJSON(needs.ghost.outputs.matrix) }}\n"),
        swap("ci", "a matrix from another output", ["WF06"], MATRIX, "      matrix: ${{ fromJSON(needs.static.outputs.runners) }}\n"),
        swap("ci", "no matrix", ["WF06"], "    strategy:\n      fail-fast: false\n" + MATRIX, ""),
        swap("ci", "the expression spaced differently", ["WF06"], "runs-on: ${{ matrix.runner }}", "runs-on: ${{matrix.runner}}"),
        swap("ci", "another key of the matrix", ["WF06"], "runs-on: ${{ matrix.runner }}", "runs-on: ${{ matrix.os }}"),
        swap("ci", "a flow matrix", ["WF06", "WF21"], MATRIX, "      matrix: {runner: [self-hosted]}\n"),
        swap("e2e", "the license job on a runner of its own", ["WF06"], "    runs-on: ubuntu-24.04\n    timeout-minutes: 60\n",
             "    runs-on: self-hosted\n    timeout-minutes: 60\n"),
    ],
    "WF07": [
        step("ci", "no persist-credentials", ["WF07"], "uses: " + CHECKOUT),
        step("ci", "true", ["WF07"], "uses: " + CHECKOUT, "with:", "  persist-credentials: true"),
        step("ci", "another way to say false", ["WF07"], "uses: " + CHECKOUT, "with:", "  persist-credentials: False"),
        step("ci", "a word", ["WF07"], "uses: " + CHECKOUT, "with:", "  persist-credentials: no"),
        step("ci", "an expression", ["WF07"], "uses: " + CHECKOUT, "with:", "  persist-credentials: ${{ inputs.keep }}"),
        step("ci", "with and nothing in it", ["WF07"], "uses: " + CHECKOUT, "with:", "  path: x"),
        step("ci", "a flow with", ["WF07", "WF21"], "uses: " + CHECKOUT, "with: {persist-credentials: false}"),
        step("ci", "capital letters", ["WF07"], "uses: Actions/Checkout@" + "a" * 40),
        step("ci", "a folder of the action", ["WF07"], "uses: actions/checkout/sub@" + "a" * 40),
        step("ci", "another letter case of the key", ["WF07"], "uses: " + CHECKOUT, "with:", "  Persist-Credentials: false"),
        swap("ci", "removed from a checkout of the file", ["WF07"], "          persist-credentials: false\n", ""),
        swap("publish", "removed from the publish lane", ["WF07"], "          persist-credentials: false\n", ""),
    ],
    "WF08": [
        step("ci", "the token in an env", ["WF08"], "env:", "  TOKEN: ${{ secrets.GITHUB_TOKEN }}", "run: echo hi"),
        step("ci", "another secret", ["WF08"], "env:", "  TOKEN: ${{ secrets.NPM_TOKEN }}", "run: echo hi"),
        step("ci", "the key of the needed catalogs in a step that does not fetch them", ["WF08"], "env:",
             "  NEEDS_DEPLOY_KEY: ${{ secrets.NEEDS_DEPLOY_KEY }}", "run: echo hi"),
        step("ci", "the key of the needed catalogs under another name", ["WF08"], "env:", "  KEY: ${{ secrets.NEEDS_DEPLOY_KEY }}",
             "run: python3 -I lab/lab-ui/catalog.py ci needs catalog"),
        step("ci", "the Lab key as an ssh key", ["WF08", "WF07"], "uses: " + CHECKOUT, "with:", "  ssh-key: ${{ secrets.LAB_DEPLOY_KEY }}"),
        swap("ci", "the Lab key as another input", ["WF08"], "key: ${{ secrets.LAB_DEPLOY_KEY }}", "token: ${{ secrets.LAB_DEPLOY_KEY }}"),
        step("ci", "the Lab key to another action", ["WF08"], "uses: " + PYTHON, "with:", "  key: ${{ secrets.LAB_DEPLOY_KEY }}"),
        step("ci", "capital letters", ["WF08"], "env:", "  TOKEN: ${{ SECRETS.THING }}", "run: echo hi"),
        step("ci", "spaces around the dot", ["WF08"], "env:", "  TOKEN: ${{ secrets . THING }}", "run: echo hi"),
        step("ci", "in a script", ["WF04", "WF08"], "run: |", "  echo ${{ secrets.THING }}"),
        step("ci", "a login with the token, outside the publish lane", ["WF08"], "uses: " + LOGIN, "with:",
             "  password: ${{ secrets.GITHUB_TOKEN }}"),
        swap("publish", "a login with another secret", ["WF08"], "password: ${{ secrets.REGISTRY_TOKEN || secrets.GITHUB_TOKEN }}",
             "password: ${{ secrets.NPM_TOKEN }}"),
        swap("publish", "a login with something after the secret", ["WF08"], "password: ${{ secrets.REGISTRY_TOKEN || secrets.GITHUB_TOKEN }}",
             "password: ${{ secrets.REGISTRY_TOKEN || secrets.GITHUB_TOKEN }}x"),
        swap("publish", "a login with the secret as another input", ["WF08"], "registry: ghcr.io", "registry: ${{ secrets.GITHUB_TOKEN }}"),
        step("e2e", "the license in a second step", ["WF08"], "env:", "  GRAVITEE_LICENSE: ${{ secrets.GRAVITEE_LICENSE }}", "run: echo hi", job="e2e"),
        step("ci", "the license outside the end-to-end module", ["WF08"], "env:", "  GRAVITEE_LICENSE: ${{ secrets.GRAVITEE_LICENSE }}", "run: echo hi"),
        job_key("e2e", "the license for the whole job", ["WF08"], "e2e", "env:", "  GRAVITEE_LICENSE: ${{ secrets.GRAVITEE_LICENSE }}"),
        step("e2e", "the registry token in a step that runs the Lab", ["WF08"], "env:",
             "  REGISTRY_PASSWORD: ${{ secrets.GRAVITEE_REGISTRY_PASSWORD }}",
             "run: docker login graviteeio.azurecr.io && make init", job="e2e"),
        step("e2e", "the registry token in a step that logs in elsewhere", ["WF08"], "env:",
             "  REGISTRY_PASSWORD: ${{ secrets.GRAVITEE_REGISTRY_PASSWORD }}", "run: docker login example.com", job="e2e"),
        step("ci", "the registry token outside the end-to-end module", ["WF08"], "env:",
             "  REGISTRY_PASSWORD: ${{ secrets.GRAVITEE_REGISTRY_PASSWORD }}", "run: docker login graviteeio.azurecr.io"),
        step("e2e", "the registry token under another name", ["WF08"], "env:",
             "  TOKEN: ${{ secrets.GRAVITEE_REGISTRY_PASSWORD }}", "run: docker login graviteeio.azurecr.io", job="e2e"),
        job_key("ci", "secrets: inherit", ["WF08"], "static", "secrets: inherit"),
        # the Lab action is the one of the catalog's own folder, not any folder that ends in .github/actions/lab
        step("ci", "the Lab key to a lab action in another folder", ["WF08"], "uses: ./other/.github/actions/lab", "with:",
             "  key: ${{ secrets.LAB_DEPLOY_KEY }}"),
        step("ci", "the Lab key to a lab action in a folder of the content", ["WF08"], "uses: ./playgrounds/x/.github/actions/lab",
             "with:", "  key: ${{ secrets.LAB_DEPLOY_KEY }}"),
        # and in every place a value can be: the items of a list, a flow list, a condition, a name, the line of a block
        step("ci", "a secret in a list item", ["WF08"], "uses: x/y@" + "a" * 40, "with:", "  args:", "    - ${{ secrets.FOO }}"),
        step("ci", "a secret in a flow list", ["WF08"], "uses: x/y@" + "a" * 40, "with:", "  args: ['${{ secrets.FOO }}']"),
        step("ci", "a secret in a condition", ["WF08"], "if: secrets.FOO != ''", "run: echo hi"),
        step("ci", "a secret in a name", ["WF08"], "name: ${{ secrets.FOO }}", "run: echo hi"),
        step("ci", "a secret in the third line of a block", ["WF04", "WF08"], "run: |", "  echo one", "  echo two", "  echo ${{ secrets.FOO }}"),
        step("ci", "a secret in an expression over two lines of a block", ["WF08"], "env:", "  X: |", "    ${{ format('{0}',", "      secrets.FOO) }}", "run: echo hi"),
        # GitHub ends an expression at the first `}}` outside a string, so what follows a `}}` in a string is still the expression
        step("ci", "a secret after a closing brace pair in a string", ["WF08"], "env:", "  X: ${{ format('}}', secrets.FOO) }}", "run: echo hi"),
        step("ci", "a secret after a closing brace pair in a string of a condition", ["WF08"], "if: ${{ '}}' != '' && secrets.FOO != '' }}",
             "run: echo hi"),
        step("ci", "a secret after a closing brace pair and more strings than are followed one by one", ["WF08"], "env:",
             "  X: ${{ format(" + "'a', " * (wf.MAX_QUOTES + 10) + "'}}', secrets.FOO) }}", "run: echo hi"),
    ],
    "WF09": [
        job_key("ci", "a job of ci.yml writes issues", ["WF09"], "static", "permissions:", "  issues: write"),
        job_key("ci", "a job of ci.yml writes pull requests", ["WF09"], "static", "permissions:", "  pull-requests: write"),
        job_key("ci", "in capital letters", ["WF09"], "static", "permissions:", "  issues: WRITE"),
        job_key("ci", "in a flow mapping", ["WF21"], "static", "permissions: {issues: write}"),
        job_key("ci", "another letter case of the key", ["WF09", "WF21"], "static", "Permissions:", "  issues: write"),
        job_key("e2e", "the license job writes", ["WF09"], "e2e", "permissions:", "  contents: write"),
        swap("publish", "the plan writes", ["WF09"], "      contents: read\n      checks: read\n", "      contents: write\n      checks: read\n"),
        swap("publish", "the release writes packages", ["WF09"], "    permissions:\n      contents: write\n",
             "    permissions:\n      contents: write\n      packages: write\n"),
        swap("publish", "the images job writes contents", ["WF09"], IMAGES,
             "    environment: publish\n    permissions:\n      contents: write\n      packages: write\n    outputs:"),
        swap("publish", "the attest job writes issues", ["WF09"], ATTEST, ATTEST + "\n      issues: write"),
        swap("nightly", "a job of the nightly run other than report", ["WF09"],
             "    timeout-minutes: 15\n    permissions:\n      contents: read\n",
             "    timeout-minutes: 15\n    permissions:\n      contents: read\n      issues: write\n"),
        swap("nightly", "the report job writes contents", ["WF09"], "    permissions:\n      issues: write\n",
             "    permissions:\n      issues: write\n      contents: write\n"),
    ],
    "WF10": [
        swap("publish", "images without an environment", ["WF10"], IMAGES, "    permissions:\n      contents: read\n      packages: write\n    outputs:"),
        swap("publish", "attest without an environment", ["WF10"], ATTEST, "    permissions:\n      contents: read\n      packages: write\n      id-token: write"),
        swap("publish", "an environment with no name", ["WF10"], "    environment: publish\n    permissions:\n      contents: read\n      packages: write\n    outputs:",
             "    environment:\n    permissions:\n      contents: read\n      packages: write\n    outputs:"),
        job_key("ci", "a job of ci.yml with steps", ["WF09", "WF10"], "static", "permissions:", "  packages: write"),
    ],
    "WF11": [
        swap("ci", "none", ["WF11"], CONCURRENCY, ""),
        swap("ci", "no group", ["WF11"], "  group: ci-${{ github.event_name }}-${{ github.event_name == 'push' && github.sha || github.ref }}\n", ""),
        swap("ci", "an empty group", ["WF11"], "  group: ci-${{ github.event_name }}-${{ github.event_name == 'push' && github.sha || github.ref }}\n",
             "  group:\n"),
        swap("ci", "an empty concurrency", ["WF11"], CONCURRENCY, "concurrency:\n\n"),
        swap("publish", "the publish lane", ["WF11"], "concurrency:\n  group: publish-${{ github.ref }}\n  cancel-in-progress: false\n\n", ""),
        swap("nightly", "the nightly run", ["WF11"], "concurrency:\n  group: nightly\n  cancel-in-progress: false\n\n", ""),
    ],
    "WF12": [
        step("ci", "curl into bash", ["WF12"], "run: curl -fsSL https://example.org/install.sh | bash"),
        step("ci", "wget into sh", ["WF12"], "run: wget -qO- https://example.org/x | sh"),
        step("ci", "curl into sudo bash", ["WF12"], "run: curl -fsSL https://example.org/x | sudo -E bash -s --"),
        step("ci", "a pipe on the next line", ["WF12"], "run: |", "  curl -fsSL https://example.org/x \\", "    | bash"),
        step("ci", "a pipe at the end of a line", ["WF12"], "run: |", "  curl -fsSL https://example.org/x |", "    bash"),
        step("ci", "a process substitution", ["WF12"], "run: bash <(curl -fsSL https://example.org/x)"),
        step("ci", "a command substitution", ["WF12"], 'run: sh -c "$(curl -fsSL https://example.org/x)"'),
        step("ci", "eval of a download", ["WF12"], 'run: eval "$(wget -qO- https://example.org/x)"'),
        step("ci", "source of a download", ["WF12"], "run: source <(curl -s https://example.org/x)"),
        step("ci", "python with a version", ["WF12"], "run: curl -fsSL https://example.org/x | env FOO=1 python3.11 -"),
        step("ci", "PowerShell", ["WF12"], "run: iwr https://example.org/x | iex"),
        step("ci", "a pipe of both outputs", ["WF12"], "run: curl https://example.org/x |& bash"),
        step("ci", "a stage between", ["WF12"], "run: curl -s https://example.org/x | tee x.sh | sh"),
        step("ci", "with a path to the shell", ["WF12"], "run: curl -s https://example.org/x | /usr/bin/env bash"),
        step("ci", "after another command", ["WF12"], "run: echo start && curl -s https://example.org/x | sh"),
        step("ci", "a double-quoted script", ["WF12"], 'run: "curl -s https://example.org/x | sh"'),
        # the spellings of the same pipe that the first version did not see: by path, quoted, a variable, a group, a wrapper's options
        *[step("ci", "a pipe: " + command, ["WF12"], "run: |", "  " + command) for command in (
            "/usr/bin/curl -sSL https://example.org/x | sh", "/usr/local/bin/wget -qO- https://example.org/x | bash",
            "env -i /usr/bin/curl https://example.org/x | sh", "curl https://example.org/x | 'bash'", 'curl https://example.org/x | "bash"',
            "curl https://example.org/x | " + BS + "bash", "curl https://example.org/x | ba''sh", "curl https://example.org/x | $SHELL",
            "curl https://example.org/x | ${SHELL}", "curl https://example.org/x | $BASH", "curl https://example.org/x | (sh)",
            "curl https://example.org/x | { sh; }", "{ curl https://example.org/x; } | sh", "(curl https://example.org/x; echo) | sh",
            "curl https://example.org/x | sudo -u root bash", "curl https://example.org/x | sudo --user root bash",
            "curl https://example.org/x | nice -n 5 bash", "curl https://example.org/x | timeout 10 bash",
            "curl https://example.org/x | xargs -I{} sh -c '{}'", "curl https://example.org/x | env -i A=1 bash -s",
            'curl "https://example.org/?a=1;b=2" | sh', '/bin/sh -c "$(curl https://example.org/x)"')],
    ],
    "WF13": [
        job_key("ci", "id-token in ci.yml", ["WF09", "WF13"], "static", "permissions:", "  id-token: write"),
        swap("publish", "id-token in the images job", ["WF09", "WF13"], IMAGES,
             "    environment: publish\n    permissions:\n      contents: read\n      packages: write\n      id-token: write\n    outputs:"),
        swap("ci", "id-token at the top", ["WF03", "WF13"], TOP_PERMISSIONS,
             "permissions:\n  contents: read\n  id-token: write\n\nconcurrency:\n  group: ci-"),
        job_key("ci", "id-token in a flow mapping", ["WF21"], "static", "permissions: {id-token: write}"),
        step("ci", "the exporter of the runtime token", ["WF13"], "uses: crazy-max/ghaction-github-runtime@" + "b" * 40),
        step("ci", "the runtime token in a script", ["WF13"], 'run: echo "$ACTIONS_RUNTIME_TOKEN"'),
        step("ci", "the cache address in an env", ["WF13"], "env:", "  ACTIONS_CACHE_URL: x", "run: echo hi"),
        step("ci", "the results address", ["WF13"], "env:", "  ACTIONS_RESULTS_URL: x", "run: echo hi"),
        swap("publish", "the runtime token in the publish lane", ["WF13"], "        with:\n          name: records\n          path: records.json\n",
             "        env:\n          T: ${{ env.ACTIONS_RUNTIME_TOKEN }}\n        with:\n          name: records\n          path: records.json\n"),
    ],
    "WF14": [
        step("ci", "a step", ["WF14"], "run: exit 1", "continue-on-error: true"),
        job_key("ci", "a job", ["WF14"], "static", "continue-on-error: true"),
        step("ci", "an expression", ["WF14"], "run: exit 1", "continue-on-error: ${{ matrix.experimental }}"),
        step("ci", "a quoted word", ["WF14"], "run: exit 1", "continue-on-error: 'true'"),
        step("ci", "a capital letter", ["WF14"], "run: exit 1", "continue-on-error: True"),
        step("ci", "another letter case of the key", ["WF14", "WF21"], "run: exit 1", "Continue-On-Error: true"),
        swap("ci", "the Lab's own check", ["WF14"], "        name: Check\n", "        name: Check\n        continue-on-error: true\n"),
    ],
    "WF17": [
        step("ci", "actions/cache", ["WF17"], "uses: actions/cache@" + "c" * 40, "with:", "  path: x", "  key: y"),
        step("ci", "restore", ["WF17"], "uses: actions/cache/restore@" + "c" * 40, "with:", "  path: x", "  key: y"),
        step("ci", "save", ["WF17"], "uses: actions/cache/save@" + "c" * 40, "with:", "  path: x", "  key: y"),
        step("ci", "a setup action with cache", ["WF17"], "uses: " + PYTHON, "with:", '  python-version: "3.14"', "  cache: pip"),
        step("ci", "a cache input in capital letters", ["WF17"], "uses: " + PYTHON, "with:", "  Cache: pip"),
        step("ci", "an expression for the cache", ["WF17"], "uses: " + PYTHON, "with:", "  cache: ${{ inputs.cache }}"),
        step("ci", "the Ruby cache", ["WF17"], "uses: ruby/setup-ruby@" + "d" * 40, "with:", "  bundler-cache: true"),
        step("ci", "cache-from", ["WF17"], 'run: docker buildx build --cache-from type=local,src=x .'),
        step("ci", "cache-to", ["WF17"], 'run: docker buildx build --cache-to=type=local,dest=x .'),
        step("ci", "cache_to", ["WF17"], 'run: docker buildx build --cache_to=x .'),
        step("ci", "cache-from as an input", ["WF17"], "uses: docker/build-push-action@" + "e" * 40, "with:", "  cache-from: foo"),
        step("ci", "type=gha", ["WF17"], 'run: docker buildx build --output type=gha .'),
        step("ci", "type = registry", ["WF17"], "run: echo type = registry"),
        step("ci", "the cache of setup-uv", ["WF17"], "uses: astral-sh/setup-uv@" + "f" * 40, "with:", "  enable-cache: true"),
        step("ci", "setup-uv, which caches unless it is told not to", ["WF17"], "uses: astral-sh/setup-uv@" + "f" * 40),
        step("ci", "setup-go, which caches unless it is told not to", ["WF17"], "uses: actions/setup-go@" + "9" * 40),
        step("ci", "setup-go with other inputs", ["WF17"], "uses: actions/setup-go@" + "9" * 40, "with:", "  go-version: stable"),
    ],
    "WF19": [
        step("ci", "an env file", ["WF19"], 'run: echo "VERSION=$VERSION" >> $GITHUB_ENV'),
        step("ci", "a quoted env file", ["WF19"], 'run: echo "VERSION=$VERSION" >> "$GITHUB_ENV"'),
        step("ci", "the path file", ["WF19"], 'run: echo "$HOME/bin" >> "$GITHUB_PATH"'),
        step("ci", "with tee", ["WF19"], 'run: echo "X=1" | tee -a "${GITHUB_ENV}"'),
        step("ci", "a heredoc", ["WF19"], "run: |", '  cat >> "$GITHUB_ENV" <<EOF', "  X=1", "  EOF"),
        step("ci", "a workflow command", ["WF19"], 'run: echo "::set-env name=A::b"'),
        step("ci", "the other workflow command", ["WF19"], 'run: echo "::add-path::/x"'),
        step("ci", "in Python", ["WF19"], "run: |", "  python3 -c \"import os; open(os.environ['GITHUB_ENV'], 'a').write('X=1')\""),
        step("ci", "a script with an expression", ["WF19"], "uses: " + SCRIPT, "with:", "  script: |", "    console.log('${{ github.head_ref }}')"),
        step("ci", "a one-line script with an expression", ["WF19"], "uses: " + SCRIPT, "with:", '  script: "console.log(\'${{ github.head_ref }}\')"'),
        step("ci", "exportVariable in a script", ["WF19"], "uses: " + SCRIPT, "with:",
             "  script: core.exportVariable('X', context.payload.pull_request.title)"),
        step("ci", "addPath in a script", ["WF19"], "uses: " + SCRIPT, "with:", "  script: core.addPath('/tmp/x')"),
        step("ci", "the env file in a script", ["WF19"], "uses: " + SCRIPT, "with:", "  script: |",
             "    require('fs').appendFileSync(process.env.GITHUB_ENV, 'X=1')"),
    ],
    "WF20": [
        job_key("ci", "unsecure commands", ["WF20"], "static", "env:", "  ACTIONS_ALLOW_UNSECURE_COMMANDS: true"),
        step("ci", "unsecure commands in a script", ["WF20"], "run: export ACTIONS_ALLOW_UNSECURE_COMMANDS=true"),
        step("ci", "an artifact of another run", ["WF20"], "uses: " + DOWNLOAD, "with:", "  run-id: 123"),
        step("ci", "a token for the artifacts of another run", ["WF20"], "uses: " + DOWNLOAD, "with:", "  github-token: ${{ github.token }}"),
        step("ci", "a secret by a computed name", ["WF20"], "env:", "  X: ${{ secrets[matrix.name] }}", "run: echo hi"),
        step("ci", "a secret by a name in brackets", ["WF20"], "env:", "  X: ${{ secrets['THING'] }}", "run: echo hi"),
        step("ci", "every secret", ["WF20"], "env:", "  X: ${{ toJSON(secrets) }}", "run: echo hi"),
        step("ci", "every secret, spelt differently", ["WF20"], "env:", "  X: ${{ toJson(secrets) }}", "run: echo hi"),
        step("ci", "every secret, in a function", ["WF20"], "env:", "  X: ${{ format('{0}', secrets) }}", "run: echo hi"),
        step("ci", "every secret, parsed", ["WF20"], "env:", "  X: ${{ fromJSON(toJSON(secrets)).THING }}", "run: echo hi"),
        step("ci", "every secret, in a condition", ["WF20"], "if: toJSON(secrets) != ''", "run: echo hi"),
        step("ci", "every secret, in a list item", ["WF20"], "uses: x/y@" + "a" * 40, "with:", "  args:", "    - ${{ toJSON(secrets) }}"),
        step("ci", "every secret, after a closing brace pair in a string", ["WF20"], "env:", "  X: ${{ format('}}', toJSON(secrets)) }}", "run: echo hi"),
    ],
    "WF21": [
        swap("ci", "a flow value of runs-on", ["WF06", "WF21"], RUNNER, "    runs-on: [ubuntu-24.04]\n    timeout-minutes: 15\n"),
        swap("ci", "a flow value of on", ["WF21"], ON_CI, "on: [pull_request, push]\n"),
        swap("ci", "a flow value of permissions", ["WF03", "WF21"], "permissions:\n  contents: read\n", "permissions: {contents: read}\n"),
        job_key("ci", "a flow value of secrets", ["WF21"], "static", "secrets: {A: b}"),
        job_key("ci", "a flow value of uses", ["WF02", "WF21"], "static", "uses: [o/r/.github/workflows/x.yml@" + "0" * 40 + "]"),
        swap("publish", "a flow value of environment", ["WF21"], IMAGES,
             "    environment: [publish]\n    permissions:\n      contents: read\n      packages: write\n    outputs:"),
        swap("ci", "a flow mapping anywhere", ["WF21"], "  cancel-in-progress: ${{ github.event_name == 'pull_request' }}\n",
             "  cancel-in-progress: ${{ github.event_name == 'pull_request' }}\nenv: {A: b}\n"),
        swap("ci", "a flow collection inside one", ["WF21"], "branches: [main]", "branches: [[main]]"),
        swap("ci", "a flow collection that does not close", ["WF21"], "branches: [main]", "branches: [main"),
        swap("ci", "a flow collection of a thousand words", ["WF21"], "branches: [main]", "branches: [" + ", ".join(["main"] * 600) + "]"),
        swap("ci", "an anchor", ["WF21"], "  group: ci-", "  group: &name ci-"),
        swap("ci", "an alias", ["WF21"], "  group: ci-${{ github.event_name }}", "  group: *name"),
        swap("ci", "a merge key", ["WF21"], "  cancel-in-progress:", "  <<: *base\n  cancel-in-progress:"),
        swap("ci", "a tag", ["WF21"], "name: CI", "name: !!str CI"),
        swap("ci", "a tag on a condition", ["WF21"], "if: ${{ !cancelled() }}", "if: !cancelled()"),
        swap("ci", "an anchor in a flow list", ["WF21"], "branches: [main]", "branches: [&a main]"),
        swap("ci", "a quoted key", ["WF21"], "  cancel-in-progress:", '  "cancel-in-progress":'),
        swap("ci", "a single-quoted key", ["WF21"], "name: CI", "'name': CI"),
        swap("ci", "a quoted key named uses", ["WF21"], "        uses: " + CHECKOUT, "        'uses': " + CHECKOUT),
        swap("ci", "a key twice", ["WF21"], "  cancel-in-progress:", "  group: other\n  cancel-in-progress:"),
        swap("ci", "a job twice", ["WF21"], "\njobs:\n", "\njobs:\n  static:\n    runs-on: ubuntu-24.04\n    timeout-minutes: 1\n"),
        swap("ci", "the top-level key jobs in capital letters", ["WF21"], "\njobs:\n", "\nJobs:\n"),
        swap("ci", "a key in capital letters", ["WF05", "WF21"], "    timeout-minutes: 15\n", "    Timeout-Minutes: 15\n"),
        swap("ci", "a quote that is not closed on its line", ["WF21"], "name: CI", "name: 'CI"),
        swap("ci", "a double quote that is not closed on its line", ["WF21"], "name: CI", 'name: "CI'),
        step("ci", "a script over two lines of a quote", ["WF21"], 'run: "echo $' + BS, '    {{ github.head_ref }}"'),
        step("ci", "a plain scalar on two lines", ["WF21"], "run: echo one", "    ${{ github.head_ref }}"),
        step("ci", "an escape that spells a dollar", ["WF21"], 'run: "echo ' + BS + 'x24{{ github.head_ref }}"'),
        step("ci", "an escape that spells a secret", ["WF21"], "env:", '  X: "${{ s' + BS + 'x65crets.FOO }}"', "run: echo hi"),
        step("ci", "an escape in a flow list", ["WF21"], "uses: x/y@" + "a" * 40, "with:", '  args: ["${{ s' + BS + 'x65crets.FOO }}"]'),
        swap("ci", "text after a quoted value", ["WF21"], "name: CI", "name: 'CI' x"),
        swap("ci", "a block header the rules do not know", ["WF21"], "run: python3 -I lab/lab-ui/catalog.py index catalog --out catalog.json",
             "run: |x"),
        swap("ci", "a block header with text", ["WF21"], "run: python3 -I lab/lab-ui/catalog.py index catalog --out catalog.json",
             "run: | echo hi"),
        swap("ci", "a control character", ["WF21"], "name: CI", "name: C\x07I"),
        swap("ci", "a null character", ["WF21"], "name: CI", "name: C\x00I"),
        swap("ci", "a line separator", ["WF21"], "name: CI", "name: C\u2028I"),
        swap("ci", "a next line character", ["WF21"], "name: CI", "name: C\x85I"),
        swap("ci", "a delete character", ["WF21"], "name: CI", "name: C\x7fI"),
        swap("ci", "a byte order mark inside", ["WF21"], "name: CI", "name: C\ufeffI", forms=("as it is", "Windows line ends")),
        swap("ci", "a tab in the indentation", ["WF21"], "  push:\n", "\tpush:\n", forms=("as it is", "Windows line ends")),
        swap("ci", "a second document", ["WF21"], "\njobs:\n", "\n---\njobs:\n"),
        swap("ci", "the end of the document, then more", ["WF21"], "\njobs:\n", "\n...\njobs:\n"),
        swap("ci", "a directive", ["WF21"], "name: CI", "%YAML 1.2\nname: CI"),
        swap("ci", "a sequence at the top", ["WF03", "WF11", "WF21"], "name: CI\n", "- name\n"),
        swap("ci", "indentation that belongs to no key", ["WF21"], "name: CI\n", "name: CI\n    deeper: x\n"),
        swap("ci", "a line to the left", ["WF07", "WF21"], "        uses: " + CHECKOUT, "  uses: " + CHECKOUT),
        swap("ci", "a key under a value", ["WF21"], "name: CI\n", "name: CI\n  other: x\n"),
        swap("ci", "a line that is not an entry", ["WF21"], "name: CI\n", "name: CI\njust some words\n"),
        swap("ci", "a complex key", ["WF21"], "name: CI\n", "name: CI\n? x\n"),
        swap("ci", "sixty levels of nesting", ["WF03", "WF11", "WF21"], "name: CI\n", "name: CI\n" + "".join(" " * (2 * i) + "k:\n" for i in range(60)) + " " * 120 + "end: x\n",
             forms=("as it is",)),
        swap("ci", "twenty thousand lines", ["WF03", "WF11", "WF21"], "name: CI\n", "name: CI\n" + "# x\n" * 20001, forms=("as it is",)),
    ],
}


NEAR = {
    "WF01": [
        swap("ci", "a scalar event", [], ON_CI, "on: pull_request\n"),
        swap("ci", "a quoted scalar event", [], ON_CI, 'on: "pull_request"\n'),
        swap("ci", "a block list of events", [], ON_CI, "on:\n  - pull_request\n  - push\n"),
        swap("ci", "filters on an event", [], "    branches: [main]\n", "    branches:\n      - main\n    paths-ignore:\n      - docs/**\n"),
    ],
    "WF02": [
        step("ci", "a pin in quotes", [], 'uses: "%s"' % PYTHON),
        step("ci", "a path inside an action", [], "uses: docker/login-action/sub@" + "a" * 40),
        step("ci", "a folder of this repository", [], "uses: ./local/action"),
        step("ci", "a folder with a dot in its name", [], "uses: ./.github/actions/other"),
    ],
    "WF03": [
        swap("ci", "a quoted value", [], "contents: read", 'contents: "read"'),
        swap("ci", "a single-quoted value", [], "contents: read", "contents: 'read'"),
        swap("ci", "a comment after the value", [], "contents: read", "contents: read   # the least"),
    ],
    "WF04": [
        step("ci", "an expression in env", [], "env:", "  TITLE: ${{ github.event.pull_request.title }}", 'run: echo "$TITLE"'),
        step("ci", "an expression in a comment of a script line", [], "run: echo hi # ${{ github.head_ref }}"),
        step("ci", "an expression in a name", [], "name: ${{ matrix.arch }}", "run: echo hi"),
        step("ci", "an expression in a condition", [], "if: ${{ github.event_name == 'push' }}", "run: echo hi"),
        step("ci", "an expression in an input", [], "uses: " + PYTHON, "with:", "  python-version: ${{ matrix.python }}"),
        step("ci", "a variable called RUN", [], "env:", "  RUN: ${{ github.run_id }}", "run: echo hi"),
    ],
    "WF05": [
        swap("ci", "a call job", [], "  result:\n", "  call:\n    uses: ./.github/workflows/x.yml\n\n  result:\n"),
        swap("ci", "the longest timeout", [], "timeout-minutes: 15", "timeout-minutes: 360"),
    ],
    "WF06": [
        swap("ci", "a quoted label", [], RUNNER, "    runs-on: 'ubuntu-24.04-arm'\n    timeout-minutes: 15\n"),
        swap("ci", "the label on the next line", [], RUNNER, "    runs-on:\n      ubuntu-24.04\n    timeout-minutes: 15\n"),
    ],
    "WF07": [
        step("ci", "a quoted false", [], "uses: " + CHECKOUT, "with:", "  persist-credentials: \"false\""),
        step("ci", "false after other inputs", [], "uses: " + CHECKOUT, "with:", "  path: x", "  fetch-depth: 0", "  persist-credentials: false"),
        step("ci", "another action", [], "uses: " + PYTHON),
    ],
    "WF08": [
        step("ci", "a secret in a comment", [], "run: echo hi # ${{ secrets.THING }}"),
        step("ci", "the Lab key to the lab action at the root of the checkout", [], "uses: ./.github/actions/lab", "with:",
             "  key: ${{ secrets.LAB_DEPLOY_KEY }}"),
        step("ci", "the Lab key to the lab action of the catalog folder", [], "uses: ./catalog/.github/actions/lab", "with:",
             "  key: ${{ secrets.LAB_DEPLOY_KEY }}"),
        step("ci", "the Lab key as a double-quoted key", [], "uses: ./catalog/.github/actions/lab", "with:",
             '  key: "${{ secrets.LAB_DEPLOY_KEY }}"'),
        # a secret is read inside `${{ }}` or an `if:`: a file, a host, a resource or a sentence that holds the word is no read
        step("ci", "a script called load-secrets", [], "run: ./scripts/load-secrets.sh"),
        step("ci", "a file called secrets.yaml", [], "run: cat deploy/secrets.yaml"),
        step("ci", "a host called my-secrets", [], "run: curl -fsS https://my-secrets.example.com/health"),
        step("ci", "a Kubernetes resource", [], "run: kubectl get secrets.v1 -A"),
        step("ci", "a sentence in a name", [], "name: Rotate the secrets. Then continue", "run: echo hi"),
        step("ci", "a step called load-secrets", [], "if: steps.load-secrets.outcome == 'success'", "run: echo hi"),
        step("ci", "a word in a string of an expression", [], "if: ${{ contains(github.event.head_commit.message, 'secrets.FOO') }}",
             "run: echo hi"),
        step("ci", "a word in a string that holds a closing brace pair", [],
             "if: ${{ contains(github.event.head_commit.message, '}} secrets.FOO') }}", "run: echo hi"),
        swap("publish", "the job's token alone", [], "password: ${{ secrets.REGISTRY_TOKEN || secrets.GITHUB_TOKEN }}",
             "password: ${{ secrets.GITHUB_TOKEN }}"),
        swap("publish", "the two in the other order", [], "password: ${{ secrets.REGISTRY_TOKEN || secrets.GITHUB_TOKEN }}",
             "password: ${{ secrets.GITHUB_TOKEN || secrets.REGISTRY_TOKEN }}"),
    ],
    "WF09": [
        job_key("publish", "a job that reads", [], "guard", "permissions:", "  contents: read"),
        job_key("ci", "a job that reads packages", [], "static", "permissions:", "  packages: read"),
        job_key("ci", "a job that gives a scope up", [], "static", "permissions:", "  checks: none"),
    ],
    "WF10": [
        swap("publish", "an environment given by an expression", [], IMAGES,
             "    environment: ${{ github.ref_type == 'tag' && 'release' || 'publish' }}\n    permissions:\n      contents: read\n"
             "      packages: write\n    outputs:"),
        swap("publish", "an environment with a name and a url", [], IMAGES,
             "    environment:\n      name: publish\n    permissions:\n      contents: read\n      packages: write\n    outputs:"),
    ],
    "WF11": [
        swap("ci", "a group written short", [], CONCURRENCY, "concurrency: ci-${{ github.ref }}\n\n"),
        swap("ci", "a group with a constant", [], CONCURRENCY, "concurrency:\n  group: ci\n"),
    ],
    "WF12": [
        step("ci", "a download and then a check", [], "run: |", "  echo curl | cat", "  curl -o f https://example.org/x && bash f"),
        step("ci", "a download into jq", [], "run: curl -fsSL https://example.org/x | jq ."),
        step("ci", "a download into tee", [], "run: curl -fsSL https://example.org/x | tee out.json"),
        step("ci", "a download into tar behind sudo, in a folder called node", [],
             "run: curl -fsSL https://example.org/node.tar.xz | sudo tar -xJ -C /opt/node"),
        step("ci", "a download into tar, in a folder called bash", [], "run: curl -fsSL https://example.org/x.tgz | sudo -E tar -xz -C /usr/local/bin bash"),
        step("ci", "a download into a command that starts like a shell", [], "run: curl -fsSL https://example.org/x | shasum -a 256"),
        step("ci", "a URL that ends in curl", [], "run: curl -fsSL https://example.org/curl | jq ."),
        step("ci", "a script of the repository called curl", [], "run: ./bin/curl https://example.org/x | cat"),
        step("ci", "a download into a sudo and a command that is no shell", [], "run: curl -fsSL https://example.org/x | sudo -u root tee /etc/x"),
        step("ci", "a group that is no pipe into a shell", [], "run: |", "  (curl -fsSL https://example.org/x; echo) | cat"),
        step("ci", "a download or a shell", [], "run: curl -s https://example.org/x || bash"),
        step("ci", "another command after a semicolon", [], "run: curl -s x; cat y | bash"),
        step("ci", "a download in a substitution that is not run", [], 'run: echo "$(curl -s https://example.org/x)"'),
        step("ci", "a version looked up", [], "run: v=$(curl -s https://example.org/v | jq -r .tag)"),
    ],
    "WF13": [
        step("ci", "the word token in a script", [], 'run: echo "token"'),
        step("ci", "an input called id-token", [], "uses: " + PYTHON, "with:", "  id-token: x"),
    ],
    "WF14": [
        step("ci", "false", [], "run: exit 0", "continue-on-error: false"),
        step("ci", "a quoted false", [], "run: exit 0", "continue-on-error: 'false'"),
    ],
    "WF17": [
        step("ci", "a cache turned off", [], "uses: " + PYTHON, "with:", "  cache: false"),
        step("ci", "the word in a comment", [], "run: echo hi # no cache here"),
        step("ci", "a variable called cache", [], "env:", "  cache: x", "run: echo hi"),
        step("ci", "another type", [], "run: docker buildx build --output type=docker ."),
        step("ci", "setup-go with the cache off", [], "uses: actions/setup-go@" + "9" * 40, "with:", "  cache: false"),
        step("ci", "setup-uv with the cache off", [], "uses: astral-sh/setup-uv@" + "f" * 40, "with:", "  enable-cache: false"),
        step("ci", "setup-node, whose cache is on only when a project asks for it", [], "uses: actions/setup-node@" + "8" * 40),
    ],
    "WF19": [
        step("ci", "a comment that names the file", [], "run: |", "  # never write to GITHUB_ENV", "  echo hi"),
        step("ci", "the output file", [], 'run: echo "x=1" >> "$GITHUB_OUTPUT"'),
        step("ci", "a comment of a script that names the file", [], "uses: " + SCRIPT, "with:", "  script: |",
             "    // never write to GITHUB_ENV", "    console.log(1)"),
        step("ci", "a script that exports nothing", [], "uses: " + SCRIPT, "with:", "  script: core.setOutput('x', 1)"),
        step("ci", "a script that reads its environment", [], "uses: " + SCRIPT, "with:", "  script: |", "    console.log(process.env.TITLE)",
             "env:", "  TITLE: ${{ github.head_ref }}"),
    ],
    "WF20": [
        step("ci", "the word secrets in a string", [], "if: ${{ contains(github.event.head_commit.message, 'secrets') }}", "run: echo hi"),
        step("ci", "an input called secrets", [], "env:", "  X: ${{ inputs.secrets }}", "run: echo hi"),
        step("ci", "an artifact of this run", [], "uses: " + DOWNLOAD, "with:", "  name: records", "  path: records"),
        step("ci", "a token in another action", [], "uses: " + PYTHON, "with:", "  github-token: ${{ github.token }}"),
        step("ci", "an input called use-secrets", [], "if: ${{ inputs.use-secrets }}", "run: echo hi"),
        step("ci", "a condition on skip-secrets", [], "if: inputs.skip-secrets == 'true'", "run: echo hi"),
        step("ci", "an env of no-secrets", [], "env:", "  A: ${{ inputs.no-secrets }}", "run: echo hi"),
    ],
    "WF21": [
        swap("ci", "a document start", [], "name: CI", "---\nname: CI"),
        swap("ci", "a flow list that is not a sensitive key", [], "branches: [main]", "branches: [main, 'release/*']"),
        swap("ci", "an empty flow mapping", [], "  cancel-in-progress:", "  x-extra: {}\n  cancel-in-progress:"),
        swap("ci", "comments and blank lines between the jobs", [], "jobs:\n", "jobs:\n  # a comment\n\n  # another\n"),
        Planted("the end of the document", "ci", lambda text: text + "...\n", [], tuple(FORMS)),
        Planted("a document start with a comment", "ci", lambda text: "--- # ci\n" + text, [], tuple(FORMS)),
    ],
}


def laid_out(case):
    """The mutated text in each spelling the case asks for: (form, text)."""
    mutated = case.mutate(text_of(case.file))
    return [(form, FORMS[form](mutated)) for form in case.forms]


class Contract(unittest.TestCase):
    def test_the_sets_are_the_ones_of_section_6_4(self):
        self.assertEqual(wf.GENERIC, {"WF01", "WF02", "WF03", "WF04", "WF05", "WF07", "WF11", "WF12", "WF14", "WF17", "WF19",
                                      "WF20", "WF21"})
        self.assertEqual(wf.LANES, {"WF06", "WF08", "WF09", "WF10", "WF13"})
        self.assertEqual(wf.ALL, wf.GENERIC | wf.LANES)
        self.assertFalse(wf.GENERIC & wf.LANES)
        self.assertEqual(sorted(wf.RULES), sorted(wf.ALL))

    def test_every_rule_has_a_planted_violation(self):
        self.assertEqual(sorted(MUTATIONS), sorted(wf.ALL))
        for code, cases in MUTATIONS.items():
            self.assertTrue(any(code in case.want for case in cases), code)

    def test_a_problem_is_the_five_fields_of_the_report(self):
        text = edit(text_of("ci"), "timeout-minutes: 15", "timeout-minutes: soon")
        found = wf.problems(path_of("ci"), text)
        self.assertEqual([(each.code, each.severity, each.path, each.line) for each in found],
                         [("WF05", "error", ".github/workflows/ci.yml", line_of(text, "  static:"))])
        self.assertEqual(found[0]._fields, ("code", "severity", "path", "line", "message"))
        self.assertEqual(found[0].message, ".github/workflows/ci.yml: job static has no timeout-minutes. Add one, so that a stuck "
                                           "build ends.")

    def test_the_module_needs_nothing_but_the_standard_library(self):
        source = Path(wf.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source, feature_version=(3, 9))
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and not node.level:
                modules.add((node.module or "").split(".")[0])
        self.assertEqual(modules, {"bisect", "re", "typing"})

    def test_the_module_writes_and_runs_nothing(self):
        """No import that could, and none of the builtins that read a file, run text or import by name."""
        tree = ast.parse(Path(wf.__file__).read_text(encoding="utf-8"))
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        self.assertEqual(names & {"open", "eval", "exec", "compile", "__import__", "input", "getattr", "globals", "vars"}, set())
        self.assertEqual(attributes & {"write", "write_text", "write_bytes", "read_text", "read_bytes", "system", "popen", "run",
                                       "Popen", "environ", "getenv"}, set())


class Passing(unittest.TestCase):
    """The files of break no rule, in every spelling."""

    def test_each_file_of_section_5_breaks_nothing(self):
        for name in FILES:
            for form, make in FORMS.items():
                with self.subTest(file=name, form=form):
                    self.assertEqual([], [each.message for each in wf.problems(path_of(name), make(text_of(name)))])

    def test_a_name_that_is_not_one_of_the_standards_has_the_rights_of_none(self):
        other = wf.problems(".github/workflows/other.yml", text_of("publish"))
        self.assertEqual(sorted({each.code for each in other}), ["WF08", "WF09", "WF13"])

    def test_the_directory_and_the_extension_are_not_the_rights(self):
        self.assertEqual([], wf.problems("publish.yml", text_of("publish"), wf.ALL))
        self.assertNotEqual([], wf.problems("publish.yaml", text_of("publish"), wf.ALL))

    def test_a_catalog_tree_is_checked_by_the_function_the_audit_calls(self):
        self.assertEqual([], wf.check(tree_of()))
        self.assertEqual([], wf.check(tree_of(), object()))

    def test_each_rule_passes_what_is_close_to_its_violations(self):
        """One or more passing fixtures for each rule: the spellings that look like a violation and are not."""
        self.assertEqual(sorted(NEAR), sorted(wf.ALL))
        for code, cases in sorted(NEAR.items()):
            for case in cases:
                for form, text in laid_out(case):
                    with self.subTest(code=code, case=case.label, form=form):
                        self.assertEqual([], codes(case.file, text))

    def test_the_sections_5_files_are_yaml_that_means_what_the_outline_says(self):
        try:
            import yaml
        except ImportError:
            print("skipped: PyYAML is not installed, so the outline is not compared with a YAML reader")
            self.skipTest("PyYAML is not installed")
        for name in FILES:
            for form in ("as it is", "indentation doubled", "sequences at the column of their key"):
                with self.subTest(file=name, form=form):
                    text = FORMS[form](text_of(name))
                    expected = yaml.load(text_of(name), Loader=yaml.BaseLoader)
                    self.assertEqual(outline(text), norm(expected))
                    self.assertEqual(norm(yaml.load(text, Loader=yaml.BaseLoader)), norm(expected))


def norm(value):
    if isinstance(value, dict):
        return {" ".join(key.split()): norm(each) for key, each in value.items()}
    if isinstance(value, list):
        return [norm(each) for each in value]
    return " ".join((value or "").split())


def outline(text):
    """What the module's reader makes of a text, as the nested dictionaries and lists of a YAML reader."""
    doc = wf._Doc("workflow.yml", text)

    def convert(node):
        if node.kind == "map":
            return {entry.name: convert(entry.value) for entry in node.entries}
        if node.kind == "seq":
            return [convert(item) for item in node.items]
        if node.kind == "flow":
            return norm(yaml.load(node.text, Loader=yaml.BaseLoader)) if yaml else node.text
        return "" if node.kind == "null" else " ".join(node.text.split())
    try:
        import yaml
    except ImportError:
        yaml = None
    return convert(doc.root)


class Mutations(unittest.TestCase):
    """A planted violation fires its rule, and only the rules it should, in every spelling of the file."""

    def test_each_rule_fails_on_each_planted_violation(self):
        for code, cases in sorted(MUTATIONS.items()):
            for case in cases:
                for form, text in laid_out(case):
                    with self.subTest(code=code, case=case.label, form=form):
                        self.assertEqual(case.want, codes(case.file, text))

    def test_the_set_decides_what_runs(self):
        """GENERIC alone does not see a violation of LANES, and LANES alone does not see one of GENERIC."""
        for code, cases in sorted(MUTATIONS.items()):
            for case in cases[:3]:
                text = case.mutate(text_of(case.file))
                with self.subTest(code=code, case=case.label):
                    only_generic = codes(case.file, text, wf.GENERIC)
                    only_lanes = codes(case.file, text, wf.LANES)
                    self.assertEqual(only_generic, [each for each in case.want if each in wf.GENERIC])
                    self.assertEqual(only_lanes, [each for each in case.want if each in wf.LANES])

    def test_a_rule_names_the_line(self):
        text = with_step(text_of("ci"), "static", "run: echo hi", "env:", "  X: ${{ secrets.NPM_TOKEN }}")
        found = wf.problems(path_of("ci"), text, {"WF08"})
        self.assertEqual([(each.code, each.line) for each in found], [("WF08", line_of(text, "NPM_TOKEN"))])
        text = with_step(text_of("ci"), "static", "run: |", "  echo one", "  echo ${{ x }}")
        found = wf.problems(path_of("ci"), text, {"WF04"})
        self.assertEqual([each.line for each in found], [line_of(text, "echo ${{ x }}")])

    def test_each_line_of_a_script_that_breaks_a_rule_is_named(self):
        text = with_step(text_of("ci"), "static", "run: |", "  echo ${{ a }}", "  echo fine", "  echo ${{ b }}")
        first = line_of(text, "echo ${{ a }}")
        self.assertEqual([each.line for each in wf.problems(path_of("ci"), text, {"WF04"})], [first, first + 2])

    def test_messages_carry_the_values_the_spec_asks_for(self):
        def message(name, text, code):
            return [each.message for each in wf.problems(path_of(name), text, {code})][0]
        path = path_of("ci")
        self.assertEqual(message("ci", edit(text_of("ci"), "  pull_request:\n  push:", "  issue_comment:\n  push:"), "WF01"),
                         path + ": the trigger issue_comment is not allowed. Use pull_request: pull_request_target and "
                                "workflow_run run with secrets on content from outsiders.")
        text = with_step(text_of("ci"), "static", "uses: actions/setup-python@v6")
        self.assertEqual(message("ci", text, "WF02"),
                         path + ":%d: actions/setup-python@v6 is not pinned to a full commit. Pin it as owner/repo@<40 hex> # tag; "
                                "Dependabot proposes the next one." % line_of(text, "setup-python"))
        self.assertEqual(message("ci", edit(text_of("ci"), RUNNER, "    runs-on: ubuntu-latest\n    timeout-minutes: 15\n"), "WF06"),
                         path + ": job static runs on ubuntu-latest. Use ubuntu-24.04 or ubuntu-24.04-arm, or a matrix that the plan "
                                "job of this file makes: a self-hosted runner keeps what a pull request leaves on it.")
        text = with_step(text_of("ci"), "static", "env:", "  X: ${{ secrets.NPM_TOKEN }}", "run: x")
        self.assertEqual(message("ci", text, "WF08"), path + ":%d: secrets.NPM_TOKEN is read where no rule allows it. Pass it by name "
                                                      "to the one step that needs it." % line_of(text, "NPM_TOKEN"))
        self.assertEqual(message("ci", in_job(text_of("ci"), "static", "permissions:", "  issues: write"), "WF09"),
                         path + ": job static holds issues: write. Only publish.yml's images, attest and release jobs, and "
                                "the report jobs of nightly.yml and versions.yml, may write.")
        self.assertEqual(message("publish", edit(text_of("publish"), IMAGES, "    permissions:\n      contents: read\n      packages: write\n    outputs:"), "WF10"),
                         ".github/workflows/publish.yml: job images writes packages without an environment. Add environment: publish.")
        text = with_step(text_of("ci"), "static", "uses: " + DOWNLOAD, "with:", "  run-id: 7")
        self.assertEqual(message("ci", text, "WF20"),
                         path + ":%d: run-id on download-artifact reads an artifact of another run, which someone may have written. "
                                "Fix: name the one secret a step needs, and download only the artifact of this run." % line_of(text, "run-id"))

    def test_a_value_that_looks_like_a_field_stays_a_value(self):
        found = wf.problems(".github/workflows/{line}.yml", edit(text_of("ci"), "timeout-minutes: 15", "timeout-minutes: soon"), {"WF05"})
        self.assertEqual(found[0].message, ".github/workflows/{line}.yml: job static has no timeout-minutes. Add one, so that a stuck "
                                           "build ends.")
        text = with_step(text_of("ci"), "static", "uses: foo/{file}@v1")
        self.assertIn("foo/{file}@v1 is not pinned", wf.problems(path_of("ci"), text, {"WF02"})[0].message)

    def test_a_value_from_the_file_is_one_short_printable_line_in_a_message(self):
        text = with_step(text_of("ci"), "static", "uses: " + "x" * 400)
        message = wf.problems(path_of("ci"), text, {"WF02"})[0].message
        self.assertLess(len(message), 400)
        self.assertIn("...", message)
        text = with_step(text_of("ci"), "static", "uses: 'a\u202eb\tc'")
        message = wf.problems(path_of("ci"), text, {"WF02"})[0].message
        self.assertNotIn("\u202e", message)
        self.assertNotIn("\t", message)

    def test_a_rule_reports_at_most_fifty_findings_in_a_file(self):
        text = text_of("ci")
        for number in range(80):
            text = with_step(text, "static", "run: echo ${{ github.head_ref }} %d" % number)
        found = wf.problems(path_of("ci"), text, {"WF04"})
        self.assertEqual(len(found), wf.MAX_PER_RULE)


class Readers(unittest.TestCase):
    """What the module reads, and what it refuses to guess."""

    def test_whatever_the_indentation_the_jobs_are_read(self):
        """The reader that looked for six spaces saw no job in a file written with four."""
        text = doubled(edit(text_of("ci"), "timeout-minutes: 15", "timeout-minutes: soon"))
        self.assertEqual(codes("ci", text), ["WF05"])
        for factor in (1, 3, 4, 7):
            with self.subTest(factor=factor):
                self.assertEqual(codes("ci", reindented(edit(text_of("ci"), "pull_request:", "pull_request_target:"), factor)), ["WF01"])
                self.assertEqual(codes("ci", reindented(edit(text_of("ci"), RUNNER, "    runs-on: self-hosted\n    timeout-minutes: 15\n"),
                                                        factor)), ["WF06"])

    def test_the_reasons_a_file_is_refused(self):
        def reason(text):
            return sorted(wf._Doc("workflow.yml", text).unreadable.values())
        self.assertEqual(reason("a: b\n"), [])
        self.assertEqual(reason("a: [b]\n"), [])
        self.assertEqual(reason("on: [b]\n"), ["a flow collection as the value of on"])
        self.assertEqual(reason("a: {b: c}\n"), ["a flow mapping"])
        self.assertEqual(reason("a: {}\n"), [])
        self.assertEqual(reason("a: [[b]]\n"), ["a flow collection inside a flow collection"])
        self.assertEqual(reason("a: &x b\n"), ["an anchor, an alias or a tag"])
        self.assertEqual(reason("a: *x\n"), ["an anchor, an alias or a tag"])
        self.assertEqual(reason("a: !!str b\n"), ["an anchor, an alias or a tag"])
        self.assertEqual(reason("'a': b\n"), ["a quoted key"])
        self.assertEqual(reason("a: b\na: c\n"), ["a key that appears twice"])
        self.assertEqual(reason("a: 'b\n"), ["a quote that is not closed on its line"])
        self.assertEqual(reason('a: "b' + BS + 'n"\n'), ["a backslash in a double-quoted scalar"])
        self.assertEqual(reason("a: b\n  c\n"), ["indentation that belongs to no key"])
        self.assertEqual(reason("a: |x\n"), ["a block scalar header the rules do not know"])
        self.assertEqual(reason("a: b\n\tc: d\n"), ["a tab in the indentation"])
        self.assertEqual(reason("a: b\n---\nc: d\n"), ["a second document, or a document marker with something after it"])
        self.assertEqual(reason("permissions: write-all\n"), ["permissions: write-all"])
        self.assertEqual(reason("Jobs:\n  a: b\n"), ["Jobs is spelt with other letters than jobs"])

    def test_a_scalar_that_is_not_a_mapping_is_not_a_workflow(self):
        for text in ("", "\n\n", "# only a comment\n", "just words\n", "- a\n- b\n", "[a, b]\n", "{a: b}\n"):
            with self.subTest(text):
                self.assertIsInstance(wf.problems(".github/workflows/ci.yml", text, wf.ALL), list)

    def test_an_empty_workflow_breaks_what_a_workflow_must_have(self):
        self.assertEqual(codes("ci", ""), ["WF03", "WF11"])

    def test_the_reader_handles_the_ways_yaml_writes_a_key(self):
        text = text_of("ci")
        # a value on the line after its key, a comment between an entry and its sequence, a sequence at the key's column
        compact_text = compact(text)
        self.assertNotEqual(compact_text, text)
        self.assertEqual(outline(compact_text), outline(text))
        self.assertEqual(outline(doubled(text)), outline(text))
        self.assertEqual(outline(text.replace("\n", "\r\n")), outline(text))

    def test_every_workflow_of_the_engine_that_is_not_the_catalogs_is_clean_of_generic(self):
        names = [path for path in sorted((ROOT / ".github" / "workflows").glob("*.yml"))]
        if not names:
            print("skipped: this checkout has no .github/workflows")
            self.skipTest("no workflows")
        for file in names:
            with self.subTest(file=file.name):
                text = file.read_text(encoding="utf-8")
                found = wf.problems(file.relative_to(ROOT).as_posix(), text, wf.GENERIC)
                self.assertEqual([each.message for each in found], [])

    def test_the_engines_ci_and_the_compose_action_pass_every_rule(self):
        found = []
        for relative in (".github/workflows/ci.yml", ".github/actions/compose/action.yml"):
            text = (ROOT / relative).read_text(encoding="utf-8")
            found += [(relative, each.code) for each in wf.problems(relative, text, wf.ALL)]
        self.assertEqual(found, [])

    def test_the_engines_privileged_workflows_break_nothing_but_the_lanes_that_are_written_for_a_catalog(self):
        """The workflows of the engine are written by other packages and change with them, so no exact set of findings is pinned
        here. What must hold: GENERIC finds nothing in any of them (the test above), so everything that `ALL` finds is a rule of
        LANES, which runs on catalogs. `e2e.yml` and `lab-images.yml` are not a catalog's `e2e.yml` and `publish.yml`: they read
        secrets and write packages by design, and that is the reason for two sets."""
        names = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
        if not names:
            print("skipped: this checkout has no .github/workflows")
            self.skipTest("no workflows")
        for file in names:
            with self.subTest(file=file.name):
                text = file.read_text(encoding="utf-8")
                found = wf.problems(file.relative_to(ROOT).as_posix(), text, wf.ALL)
                self.assertEqual([each.message for each in found if each.code not in wf.LANES], [])
                self.assertEqual([each for each in found if each.code in wf.GENERIC], [])


class Aggregator(unittest.TestCase):
    """The `run:` block of the job `result` is run with bash. A skipped, an empty or a canceled answer is red."""

    @classmethod
    def setUpClass(cls):
        cls.bash = shutil.which("bash")
        if cls.bash is None:
            print("skipped: bash is not installed, so the aggregators are not run")

    def setUp(self):
        if self.bash is None:
            self.skipTest("bash is not installed")

    def script(self, name, job="result"):
        doc = wf._Doc(path_of(name), text_of(name))
        steps = doc.get(next(each.node for each in doc.jobs if each.name == job), "steps").value.items
        lines = [line for step in steps if doc.get(step, "run") for _, line in doc.get(step, "run").value.parts]
        self.assertTrue(lines, "the job %s has a script" % job)
        return textwrap.dedent("\n".join(lines)) + "\n"

    def run_it(self, name, **env):
        done = subprocess.run([self.bash, "-c", self.script(name)], env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C", **env},
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60, check=False, universal_newlines=True)
        return done.returncode, done.stdout

    MATRIX = '{"include":[{"arch":"amd64","platform":"linux/amd64","runner":"ubuntu-24.04"}]}'
    SANE = {"STATIC": "success", "SEED": "success", "COMPILE": "success", "WANT_SEED": "true", "WANT_COMPILE": "true", "MATRIX": MATRIX}

    def ci(self, **changes):
        return self.run_it("ci", **{**self.SANE, **changes})

    def test_the_result_of_ci_is_green_for_a_sane_plan(self):
        code, output = self.ci()
        self.assertEqual(code, 0, output)
        self.assertNotIn("::error", output)

    def test_it_is_green_when_the_plan_asked_for_nothing_and_nothing_ran(self):
        """A change to a README: the plan says false twice, and the jobs it did not ask for are skipped."""
        code, output = self.ci(SEED="skipped", COMPILE="skipped", WANT_SEED="false", WANT_COMPILE="false", MATRIX="")
        self.assertEqual(code, 0, output)

    def test_it_is_green_when_a_job_the_plan_did_not_ask_for_ran_anyway(self):
        code, output = self.ci(WANT_SEED="false", MATRIX=self.MATRIX)
        self.assertEqual(code, 0, output)

    def test_it_is_green_when_the_build_was_asked_for_and_the_seed_was_not(self):
        code, output = self.ci(SEED="skipped", WANT_SEED="false")
        self.assertEqual(code, 0, output)

    def test_it_is_red_when_the_plan_outputs_are_blank(self):
        """A plan that wrote nothing (a bug, a changed writer, a truncated $GITHUB_OUTPUT) must not turn the check green."""
        for label, changes in {
            "both blank, nothing ran": {"WANT_SEED": "", "WANT_COMPILE": "", "SEED": "skipped", "COMPILE": "skipped", "MATRIX": ""},
            "both blank": {"WANT_SEED": "", "WANT_COMPILE": ""},
            "the seed answer blank": {"WANT_SEED": ""},
            "the compile answer blank": {"WANT_COMPILE": ""},
            "an answer that is not one": {"WANT_COMPILE": "yes"},
            "an answer in capital letters": {"WANT_SEED": "True"},
            "the plan itself skipped": {"WANT_SEED": "", "WANT_COMPILE": "", "MATRIX": "", "STATIC": "skipped"},
        }.items():
            with self.subTest(label):
                code, output = self.ci(**changes)
                self.assertEqual(code, 1, output)
                self.assertIn("::error::", output)

    def test_it_is_red_when_the_build_was_asked_for_and_names_no_architecture(self):
        code, output = self.ci(MATRIX="")
        self.assertEqual(code, 1, output)
        self.assertIn("named no architecture", output)

    def test_it_is_red_when_a_job_that_was_asked_for_is_skipped(self):
        for changes in ({"SEED": "skipped"}, {"COMPILE": "skipped"}, {"SEED": "skipped", "COMPILE": "skipped"}):
            with self.subTest(changes):
                code, output = self.ci(**changes)
                self.assertEqual(code, 1, output)
                self.assertIn("ended skipped", output)

    def test_it_is_red_when_a_job_is_canceled_or_failed_whatever_the_plan_asked(self):
        for job, asked in (("SEED", "WANT_SEED"), ("COMPILE", "WANT_COMPILE")):
            for result in ("cancelled", "failure"):
                for wanted in ("true", "false"):
                    with self.subTest(job=job, result=result, asked=wanted):
                        code, output = self.ci(**{job: result, asked: wanted})
                        self.assertEqual(code, 1, output)
                        self.assertIn("ended %s" % result, output)

    def test_it_is_red_when_static_did_not_succeed(self):
        for result in ("failure", "cancelled", "skipped", ""):
            with self.subTest(result):
                code, output = self.ci(STATIC=result)
                self.assertEqual(code, 1, output)
                self.assertIn("did not end well", output)

    def test_the_end_to_end_result_is_red_on_the_same_mistakes(self):
        sane = {"PLAN": "success", "E2E": "success", "COUNT": "3"}
        self.assertEqual(self.run_it("e2e", **sane)[0], 0)
        self.assertEqual(self.run_it("e2e", PLAN="success", E2E="skipped", COUNT="0")[0], 0)
        for label, changes in {"a plan that failed": {"PLAN": "failure"}, "a plan that was canceled": {"PLAN": "cancelled"},
                               "no count": {"COUNT": ""}, "a count that is not a number": {"COUNT": "x"},
                               "a skipped job that had to run": {"E2E": "skipped"}, "a canceled job": {"E2E": "cancelled"},
                               "a failed job": {"E2E": "failure"}}.items():
            with self.subTest(label):
                code, output = self.run_it("e2e", **{**sane, **changes})
                self.assertEqual(code, 1, output)

    def test_the_script_found_is_the_script_of_the_workflow(self):
        self.assertIn('fail "The plan gave no answer: open the job Checks"', self.script("ci"))
        self.assertIn('[ "$STATIC" = success ]', self.script("ci"))


class Trees(unittest.TestCase):
    """`check(tree, ctx)`: which files are read, what is not followed, and what cannot be read."""

    def tree(self, **changes):
        files = dict(files_of())
        for path, value in changes.items():
            files[path.replace("__", "/")] = value
        return Tree(files)

    def test_only_workflows_and_local_actions_are_read(self):
        bad = "on: pull_request_target\n"
        tree = Tree({**files_of(), "docs/ci.yml": bad, ".github/workflows/sub/ci.yml": bad, ".github/workflows/notes.txt": bad,
                     ".github/workflows/ci.yml.bak": bad, "playgrounds/x/.github/workflows/ci.yml": bad, ".github/actions/lab/readme.yml": bad})
        self.assertEqual(wf.check(tree), [])
        self.assertEqual(set(tree.reads), set(FILES.values()))

    def test_yaml_and_deeper_actions_are_read_too(self):
        bad = "name: x\non: pull_request_target\n"
        tree = Tree({**files_of(), ".github/workflows/extra.yaml": bad, ".github/actions/a/b/action.yaml": "name: a\nruns:\n  using: composite\n  steps:\n    - run: echo ${{ x }}\n"})
        found = wf.check(tree)
        self.assertIn(("WF01", ".github/workflows/extra.yaml"), [(each.code, each.path) for each in found])
        self.assertIn(("WF04", ".github/actions/a/b/action.yaml"), [(each.code, each.path) for each in found])

    def test_a_workflow_is_a_workflow_by_its_place_and_not_by_what_it_says(self):
        tree = Tree({**files_of(), ".github/workflows/x.yml": "name: x\n"})
        found = [(each.code, each.path) for each in wf.check(tree)]
        self.assertEqual(sorted(found), [("WF03", ".github/workflows/x.yml"), ("WF11", ".github/workflows/x.yml")])

    def test_a_file_of_the_workflows_folder_is_a_workflow_whatever_its_name(self):
        """GitHub runs every file of `.github/workflows/` as a workflow, so the place decides and the name does not: a workflow
        called action.yml is a workflow and breaks WF01, WF03 and WF06 like any other."""
        bad = ("name: x\non:\n  pull_request_target:\npermissions:\n  contents: write\njobs:\n  a:\n    runs-on: self-hosted\n"
               "    permissions:\n      packages: write\n      id-token: write\n    steps:\n      - uses: actions/checkout@v4\n      - run: echo hi\n")
        want = sorted({each.code for each in wf.problems(".github/workflows/evil.yml", bad)})
        self.assertEqual(want, ["WF01", "WF02", "WF03", "WF05", "WF06", "WF07", "WF09", "WF10", "WF11", "WF13"])
        for name in ("action.yml", "action.yaml", "ACTION.YML", "Action.yaml", "sub/action.yml"):
            path = ".github/workflows/" + name
            with self.subTest(name=name):
                if "/" not in name:
                    self.assertEqual(sorted({each.code for each in wf.problems(path, bad)}), want)
                found = wf.check(Tree({**files_of(), path: bad}))
                if "/" in name:
                    self.assertEqual([each for each in found if each.path == path], [])    # not at the root of the folder: not read
                else:
                    self.assertEqual(sorted({each.code for each in found if each.path == path}), want)

    def test_an_action_is_an_action_by_its_folder_and_without_one_by_its_name(self):
        bad = "name: x\non:\n  pull_request_target:\nruns:\n  using: composite\n  steps:\n    - run: echo hi\n"
        for path in (".github/actions/x/action.yml", ".github/actions/x/y/action.yaml", "action.yml", "sub/ACTION.yaml",
                     ".github/actions/x/workflows/ci.yml"):
            with self.subTest(path=path):
                self.assertEqual([each.code for each in wf.problems(path, bad)], [])    # WF01, WF03 and WF11 are for workflows
        for path in (".github/workflows/ci.yml", ".github/workflows/x/action.yml", "ci.yml"):
            with self.subTest(path=path):
                self.assertIn("WF01", [each.code for each in wf.problems(path, bad)])
        found = wf.check(Tree({**files_of(), ".github/actions/x/action.yml": bad}))
        self.assertEqual([each for each in found if each.path == ".github/actions/x/action.yml"], [])

    def test_a_link_a_submodule_and_a_file_too_big_are_not_read(self):
        tree = Tree({**files_of(), ".github/workflows/link.yml": b"../../elsewhere.yml", ".github/workflows/sub.yml": b"",
                     ".github/workflows/big.yml": b"x" * (wf.FILE_LIMIT + 1)},
                    modes={".github/workflows/link.yml": "120000", ".github/workflows/sub.yml": "160000"})
        found = wf.check(tree)
        self.assertEqual(sorted((each.path, each.code, each.line) for each in found),
                         [(".github/workflows/big.yml", "WF21", 1), (".github/workflows/link.yml", "WF21", 1),
                          (".github/workflows/sub.yml", "WF21", 1)])
        self.assertNotIn(".github/workflows/link.yml", tree.reads)
        self.assertNotIn(".github/workflows/sub.yml", tree.reads)
        self.assertNotIn(".github/workflows/big.yml", tree.reads)

    def test_a_tree_of_many_workflows_is_read_up_to_a_limit(self):
        good = text_of("ci")
        files = {".github/workflows/w%03d.yml" % number: good for number in range(wf.MAX_FILES + 20)}
        tree = Tree(files)
        found = wf.check(tree)
        self.assertEqual(len(tree.reads), wf.MAX_FILES)
        self.assertEqual(sorted({(each.path, each.code, each.line) for each in found if each.code == "WF21"}),
                         [(".github/workflows/w%03d.yml" % number, "WF21", 1) for number in range(wf.MAX_FILES, wf.MAX_FILES + 20)])

    def test_a_file_that_is_not_text_cannot_be_read(self):
        for label, data in {"latin-1": "name: caf\xe9\n".encode("latin-1"), "utf-16": "name: x\n".encode("utf-16"),
                            "a null": b"name: x\x00\n"}.items():
            with self.subTest(label):
                found = wf.check(Tree({**files_of(), ".github/workflows/odd.yml": data}))
                self.assertIn("WF21", [each.code for each in found if each.path == ".github/workflows/odd.yml"])

    def test_a_tree_that_cannot_give_a_file_answers_unreadable(self):
        class Silent(Tree):
            def read(self, path, limit=1048576):
                return None
        found = wf.check(Silent({".github/workflows/ci.yml": b"name: x\n"}))
        self.assertEqual([(each.code, each.line) for each in found], [("WF21", 1)])

    def test_the_problems_are_sorted_and_the_same_twice(self):
        files = dict(files_of())
        files[".github/workflows/ci.yml"] = edit(edit(files[".github/workflows/ci.yml"], "timeout-minutes: 15", "timeout-minutes: soon"),
                                                 "  pull_request:", "  issue_comment:")
        first, second = wf.check(Tree(files)), wf.check(Tree(dict(reversed(list(files.items())))))
        self.assertEqual(first, second)
        self.assertEqual(first, sorted(first, key=lambda each: (each.path, each.line, each.code)))

    def test_with_the_real_tree_of_the_audit(self):
        from lab_ui.catalogs import tree as real
        with tempfile.TemporaryDirectory() as holder:
            shutil.copytree(MANAGED, Path(holder) / "catalog")
            self.assertEqual(wf.check(real.FolderTree(Path(holder) / "catalog")), [])


# ---------------------------------------------------------------- hostile text

MB = 1024 * 1024
HEAD = ("name: x\non:\n  push:\npermissions:\n  contents: read\nconcurrency:\n  group: g\njobs:\n  a:\n    runs-on: ubuntu-24.04\n"
        "    timeout-minutes: 5\n    steps:\n")


def step_line(line):
    return HEAD + "      - " + line + "\n"


HOSTILE = {
    "a script of 1 MB": step_line("run: " + "a" * MB),
    "words in a script": step_line("run: " + "a " * (MB // 2)),
    "expressions": step_line("run: " + "${{ " * (MB // 4)),
    "pipes after a download": step_line("run: curl " + "| " * (MB // 2)),
    "or after a download": step_line("run: curl " + "|| " * (MB // 3)),
    "substitutions of downloads": step_line("run: " + "$(curl x) " * (MB // 10)),
    "a double-quoted script": step_line('run: "' + "a" * MB + '"'),
    "backslashes in a double quote": step_line('run: "' + "\\\\" * (MB // 2) + '"'),
    "escaped braces": step_line('run: "' + "\\x7b" * (MB // 4) + '"'),
    "a pile of single quotes": step_line("run: '" + "''" * (MB // 2) + "'"),
    "a quote that never closes": step_line("run: '" + "a" * MB),
    "a double quote that never closes": step_line('run: "' + "\\\"" * (MB // 2)),
    "a key made of quotes": step_line("'" * MB + ": x"),
    "a key that never closes": step_line('"' + "\\\"" * (MB // 2) + ": x"),
    "a key of colons": step_line(":" * MB + "x"),
    "a flow list that does not close": step_line("with: " + "[" * MB),
    "a flow list of a million words": step_line("with: [" + "a," * (MB // 2) + "]"),
    "dashes": step_line("- " * (MB // 2)),
    "indentation": step_line(" " * MB + "x: y"),
    "a comment": HEAD + "      # " + "c" * MB + "\n",
    "tabs": step_line("\t" * MB),
    "anchors": step_line("run: " + "&a " * (MB // 3)),
    "the word secrets": step_line("run: " + "secrets " * (MB // 8)),
    "the word secrets and spaces": step_line("run: ${{ secrets" + " " * MB + "x }}"),
    "negations": step_line("if: ${{ " + "!contains(" * (MB // 10) + " }}"),
    "tests of results": step_line("if: ${{ " + "needs.a.result != x " * (MB // 20) + "}}"),
    "controls": step_line("run: " + "\x00" * MB),
    "line separators": step_line("run: " + "\u2028" * MB),
    "lines": HEAD + "      - run: x\n" * 19000,
    "more lines than are read": "a: b\n" * 30000,
    "quotes that never close, a line each": "a: '\n" * 19000,
    "continuation lines": HEAD + "      - run: a\n" + "          b\n" * 19000,
    "a block of lines": step_line("run: |") + "        x\n" * 19000,
    "blank lines": "\n" * 19990 + "a: b\n",
    "words that are keys": step_line("run: " + "x: " * (MB // 3)),
    "three thousand jobs": HEAD[:HEAD.index("jobs:") + 6] + "".join(
        "  j%d:\n    runs-on: ubuntu-24.04\n    timeout-minutes: 5\n    permissions:\n      contents: read\n    steps:\n      - run: x\n"
        % number for number in range(3000)),
    "six thousand secrets": step_line("run: echo hi") + "        env:\n" + "".join("          K%d: ${{ secrets.S%d }}\n" % (n, n) for n in range(6000)),
    "twenty thousand tests of a result": step_line("if: ${{ " + " && ".join("needs.j%d.result != 'x'" % n for n in range(20000)) + " }}"),
    # calls, groups and strings in a condition, each of which was a quadratic scan once
    "calls that are never closed": step_line("if: ${{ " + "!contains(" * (MB // 10) + " }}"),
    "calls, a result at the end of each": step_line("if: ${{ " + "!contains(needs.a.result, " * (MB // 30) + " }}"),
    "groups that are never closed": step_line("if: ${{ " + "!(" * (MB // 2) + "needs.a.result == 'x' }}"),
    "groups that close": step_line("if: ${{ " + "!(" * (MB // 4) + "needs.a.result == 'x'" + ")" * (MB // 4) + " }}"),
    "closing parentheses": step_line("if: ${{ " + ")" * MB + " }}"),
    "results behind calls": step_line("if: ${{ " + "toJSON(needs.a.result) != x " * (MB // 30) + " }}"),
    "results compared twice": step_line("if: ${{ " + "needs.a.result == 'x' == " * (MB // 30) + "false }}"),
    "results and strings": step_line("if: ${{ " + "needs.a.result == 'a' || '' " * (MB // 30) + " }}"),
    "index syntax": step_line("if: ${{ " + "needs['a']['result'] != 'x' " * (MB // 30) + " }}"),
    "spaces in a result": step_line("if: ${{ needs" + " . a" * (MB // 8) + " }}"),
    "the word needs": step_line("if: ${{ " + "needs " * (MB // 6) + " }}"),
    # secrets and escapes, which are read in every place a value can be
    "secrets in expressions": step_line("run: " + "${{ secrets.A }} " * (MB // 17)),
    "secrets in a list": step_line("run: x") + "        with:\n          a:\n" + "            - ${{ secrets.A }}\n" * 15000,
    "secrets in a flow list": step_line("with: [" + "'${{ secrets.A }}', " * (MB // 20) + "]"),
    "secrets in one block": step_line("run: |") + "          echo ${{ secrets.A }}\n" * 18000,
    "secrets and strings": step_line("run: ${{ " + "'a' && secrets.A && " * (MB // 20) + "x }}"),
    "a secret over many lines": HEAD + "      - env:\n          A: ${{ secrets.A\n" + "            x\n" * 18000 + "            }}\n",
    "escapes": step_line('run: "' + (BS + "x41") * (MB // 4) + '"'),
    "unicode escapes": step_line('run: "' + (BS + "U00000041") * (MB // 10) + '"'),
    "escapes of the braces": step_line('run: "' + (BS + "u0024" + BS + "u007b") * (MB // 12) + '"'),
    "escapes in a flow list": step_line('with: ["' + (BS + "x41") * 300 + '"]'),
    "escapes and lines": HEAD + '      - run: "' + BS + "x41" * 1 + "\\\n" + ("  " + (BS + "x41") * 5 + BS + "\n") * 9000 + '"\n',
    "wrappers after a download": step_line("run: curl x | " + "sudo -u " * (MB // 8) + "bash"),
    "options after a download": step_line("run: curl x | sudo " + "-x " * (MB // 3)),
    "groups after a download": step_line("run: |") + "          curl x; } | cat\n" * 18000,
    "paths of commands": step_line("run: " + "/a" * (MB // 2)),
    "paths and downloads": step_line("run: " + "/usr/bin/curl " * (MB // 14)),
    "substitutions of paths": step_line("run: " + "$(/usr/bin/curl x) " * (MB // 19)),
    "caches": step_line("uses: actions/setup-go@" + "a" * 40) + "        with:\n" + "          cache: x\n" * 15000,
    "scripts of github-script": step_line("uses: actions/github-script@" + "a" * 40) + "        with:\n          script: |\n" + "            core.exportVariable(x)\n" * 15000,
}


PLACES = (".github/workflows/ci.yml", ".github/workflows/publish.yml", ".github/workflows/e2e.yml", ".github/actions/lab/action.yml")


def shapes_for(path):
    """Every shape under the two workflows that carry the most rules (the publish lane has the Docker login); under the
    end-to-end file (the license) and an action, the shapes that hold secrets, escapes or very long lines, which are the ones whose
    cost depends on the family or on the kind of file."""
    if path in PLACES[:2]:
        return list(HOSTILE.items())
    return [(label, text) for label, text in HOSTILE.items() if any(word in label for word in ("secret", "escape", "1 MB", "lines"))]


class Hostile(unittest.TestCase):
    """The input is a hostile contributor's tree: a rule finishes whatever it is given, in well under a second (the CPU time of
    the process, so that a busy machine does not fail the test), and never raises."""

    def test_every_rule_finishes_on_every_hostile_shape(self):
        """Each shape under four names, because the name gives a file the rules of its family: the publish lane has the secrets of
        a Docker login, the end-to-end file has the license, an action has no job."""
        for path in PLACES:
            for label, text in shapes_for(path):
                with self.subTest(label, path=path):
                    start = time.process_time()
                    found = wf.problems(path, text, wf.ALL)
                    spent = time.process_time() - start
                    # A rule that backtracks takes tens of seconds here; a linear one takes about 0.3 s of a laptop's CPU and three
                    # times that on a runner of GitHub, so the bound leaves the runner its room and still catches the first
                    self.assertLess(spent, 3.0, "%s as %s took %.2f s" % (label, path, spent))
                    self.assertIsInstance(found, list)
                    self.assertLessEqual(len(found), 21 * wf.MAX_PER_RULE)

    def test_a_pipe_into_a_shell_still_found_after_wrappers_and_groups(self):
        self.assertEqual(["WF12"], codes("ci", step_line("run: curl x | " + "sudo -E " * 5 + "bash")))
        self.assertEqual(["WF12"], codes("ci", step_line("run: |\n          { curl x; } | sh")))
        self.assertEqual(["WF12"], codes("ci", step_line("run: |\n          curl x; (echo) | sh")))

    def test_a_download_with_a_thousand_pipes_is_a_download_into_a_shell(self):
        self.assertEqual(codes("ci", step_line("run: curl x " + "| cat " * 2000)), ["WF12"])
        self.assertEqual(codes("ci", step_line("run: curl x " + "| cat " * 20 + "| bash")), ["WF12"])

    def test_nothing_the_text_says_is_run_or_followed(self):
        """The text is data: names that look like modules, paths or commands are just names."""
        with tempfile.TemporaryDirectory() as holder:
            marker = Path(holder) / "marker"
            text = step_line("uses: ./../../etc/passwd") + "      - run: __import__('pathlib').Path(%r).write_text('x')\n" % str(marker)
            found = wf.problems(".github/workflows/../ci.yml", text, wf.ALL)
            self.assertIn("WF02", [each.code for each in found])
            self.assertFalse(marker.exists())

    def test_random_damage_to_the_files_of_section_5_never_raises(self):
        rng = random.Random(2026)
        pieces = list("{}[]'\"&*!|>\\\t #:-,\n%@`?$") + ["${{", "}}", "\u2028", "\x00", "---", "...", "- ", ": ", "\ufeff", "\r", "\u00e9"]
        names = sorted(FILES)
        for number in range(400):
            name = rng.choice(names)
            text = text_of(name)
            for _ in range(rng.randint(1, 10)):
                kind, at = rng.random(), rng.randrange(len(text) + 1)
                if kind < 0.4:
                    text = text[:at] + rng.choice(pieces) + text[at:]
                elif kind < 0.6:
                    text = text[:at] + text[at + rng.randint(1, 30):]
                else:
                    rows = text.split("\n")
                    row = rng.randrange(len(rows))
                    rows[row] = " " * rng.randint(0, 12) + rows[row].lstrip()
                    text = "\n".join(rows)
            with self.subTest(number=number, file=name):
                start = time.process_time()
                found = wf.problems(path_of(name), text, wf.ALL)
                self.assertLess(time.process_time() - start, 1.0)
                self.assertTrue(all(isinstance(each, wf.Problem) and each.code in wf.ALL and each.line >= 0 for each in found))


# ---------------------------------------------------------------- the tree of the tests

class Entry(NamedTuple):
    mode: str
    path: str
    size: int


class Tree(object):
    """The Tree of 7.8 over a dictionary: what a rule asks to read is written down, and a link or a submodule reads as nothing."""

    def __init__(self, files, modes=None):
        self.files = {path: (value.encode("utf-8") if isinstance(value, str) else value) for path, value in files.items()}
        self.modes = modes or {}
        self.reads = []

    def entries(self):
        return [Entry(self.modes.get(path, "100644"), path, len(data)) for path, data in self.files.items()]

    def read(self, path, limit=1048576):
        self.reads.append(path)
        if path not in self.files or self.modes.get(path, "100644") not in ("100644", "100755"):
            return None
        data = self.files[path]
        return data if len(data) <= limit else None


def files_of():
    return {file.relative_to(MANAGED).as_posix(): file.read_text(encoding="utf-8") for file in sorted(MANAGED.rglob("*")) if file.is_file()}


def tree_of():
    return Tree(files_of())


if __name__ == "__main__":
    unittest.main()
