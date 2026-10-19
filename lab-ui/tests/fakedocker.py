"""A `docker` that is not Docker, for the tests of the CI stages and of the app tests: no daemon, no network, no image.

    fake = FakeDocker(self)                  # a folder with a `docker` in it; the PATH of the test starts with that folder
    fake.when("buildx", "build", code=1, stderr="#7 ERROR: ...")    # what a call that holds these words answers
    fake.when("~toomanyrequests", times=2, code=1)                  # "~text": a call whose words, joined, hold the text
    fake.calls()                                                    # every call: Call(argv, cwd, env) in order
    fake.commands("buildx", "build")                                # the argv of the calls that hold these words

`docker compose ...` is passed to the real Docker when this machine has one (it reads files and needs no daemon), so a test can
read a catalog with Compose and fake the builds and the runs. Every call is recorded (the arguments, the folder it ran in and the NAMES of the variables it had, never a value), so a test can
prove what a stage ran and what it kept from it. A rule answers with an exit code, standard output and errors, and may wait
(`sleep`, to try a time limit) or hand out a list of answers one call after the other (`answers`; the last one repeats). The
newest rule that matches wins. With no rule a call answers as a working Docker would: `version` and `info` work, `buildx
build` builds, `image inspect` knows an image of 41 MB, `pull` and `rmi` work, a `run` of an app's tests passes, and a `run`
of the gitleaks image reads the folder it is given the way gitleaks does (below).

**The gitleaks it plays.** It follows the rules of the real one that the standard relies on, so that a test fails when a stage
forgets one of them: the configuration is `--config`, else `.gitleaks.toml` of the repository; a repository's
`.gitleaksignore` is read from `--gitleaks-ignore-path`, else from the repository; a line with `gitleaks:allow` is passed over
unless `--ignore-gitleaks-allow`; an `[allowlist]` of `regexes` or `paths` that matches everything silences the scan; without
`--exit-code 0` a finding is exit 1. It finds a GitHub token (`ghp_` and 36 characters) and an AWS key (`AKIA` and 16) in the
files of the folder mounted at /repo, and writes the report where `--report-path` leads under a mount. It reads the files, never
the history and never the `--log-opts`: what git prints of a range (a file it takes for binary has no added line) is proved with
a real git, in `test_ci_gitleaks.History`.

Python 3.9 and later, standard library only. The script it writes is run with the interpreter of the test."""

import json
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path
from typing import List, NamedTuple, Optional
from unittest import mock

SCRIPT = r'''
import fcntl, json, os, re, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
argv = sys.argv[1:]


def load(name, default):
    try:
        with open(os.path.join(HERE, name), encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


with open(os.path.join(HERE, "lock"), "a") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    with open(os.path.join(HERE, "calls.jsonl"), "a", encoding="utf-8") as log:
        log.write(json.dumps({"argv": argv, "cwd": os.getcwd(), "env": sorted(os.environ)}) + "\n")
    rules = load("rules.json", [])
    state = load("state.json", {})
    answer = None
    joined = " ".join(argv)
    for number in range(len(rules) - 1, -1, -1):   # the newest rule that matches and still has answers to give
        rule = rules[number]
        if not all((word[1:] in joined) if word.startswith("~") else (word in argv) for word in rule["words"]):
            continue
        used = state.get(str(number), 0)
        if rule.get("times") is not None and used >= rule["times"]:
            continue
        state[str(number)] = used + 1
        answer = rule["answers"][min(used, len(rule["answers"]) - 1)] if rule.get("answers") else rule
        break
    with open(os.path.join(HERE, "state.json"), "w", encoding="utf-8") as handle:
        json.dump(state, handle)


def say(answer):
    if answer.get("sleep"):
        time.sleep(answer["sleep"])
    sys.stdout.write(answer.get("stdout", ""))
    sys.stderr.write(answer.get("stderr", ""))
    sys.stdout.flush()
    sys.stderr.flush()
    sys.exit(answer.get("code", 0))


if answer is not None:
    say(answer)

words = [a for a in argv if not a.startswith("-")]
first = words[0] if words else ""
second = words[1] if len(words) > 1 else ""
real = load("real.json", None)
if first == "compose" and real:   # Compose is the one thing it does not play: the real one reads the files (no daemon is needed)
    os.execv(real, [real] + argv)
if first == "version":
    say({"stdout": "27.0.0\n"})
if first == "info":
    say({})
if first == "buildx" and second == "build":
    tag = argv[argv.index("--tag") + 1] if "--tag" in argv else "image"
    say({"stdout": "#1 [internal] load build definition from Dockerfile\n#1 DONE 0.0s\n#2 [1/2] FROM python:3.14-slim\n"
                   "#2 DONE 0.5s\n#3 [2/2] COPY . /app\n#3 DONE 0.1s\n#4 exporting to image\n#4 naming to " + tag + " done\n"})
if first == "image" and second == "inspect":
    say({"stdout": "41234567\n"})
if first in ("pull", "rmi", "image"):
    say({})
if first == "run" and any("gitleaks" in a for a in argv):
    pass
elif first == "run":
    if "node" in argv:
        say({"stdout": "........\n"})
    say({"stderr": "...\n----------------------------------------------------------------------\nRan 3 tests in 0.002s\n\nOK\n"})
else:
    say({})


# The gitleaks of the standard
def mounts():
    found = {}
    for number, word in enumerate(argv):
        if word == "-v" and number + 1 < len(argv):
            host, _, rest = argv[number + 1].partition(":")
            found[rest.split(":")[0]] = host
    return found


def host_of(path, held):
    best = ""
    for target in held:
        if (path == target or path.startswith(target.rstrip("/") + "/")) and len(target) > len(best):
            best = target
    return os.path.join(held[best], path[len(best):].lstrip("/")) if best else None


def option(name, default=None):
    if name in argv and argv.index(name) + 1 < len(argv):
        return argv[argv.index(name) + 1]
    return default


held = mounts()
repo = held.get("/repo")
config_path = option("--config") or "/repo/.gitleaks.toml"
config_file = host_of(config_path, held)
config = ""
if config_file and os.path.isfile(config_file):
    with open(config_file, encoding="utf-8", errors="replace") as handle:
        config = handle.read()
silenced = "[allowlist]" in config and re.search(r"(regexes|paths)\s*=\s*\[\s*\'\'\'?\.\*", config) is not None
ignore_path = option("--gitleaks-ignore-path") or "/repo"
ignore_folder = host_of(ignore_path, held)
ignored = set()
if ignore_folder:
    candidate = ignore_folder if os.path.isfile(ignore_folder) else os.path.join(ignore_folder, ".gitleaksignore")
    if os.path.isfile(candidate):
        with open(candidate, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if line and not line.startswith("#"):
                    ignored.add(":".join(line.split(":")[-3:]))
allow_comments = "--ignore-gitleaks-allow" not in argv
rules = {"github-pat": re.compile(r"ghp_[A-Za-z0-9]{36}"), "aws-access-token": re.compile(r"AKIA[0-9A-Z]{16}")}
findings = []
if repo and not silenced:
    for top, folders, names in os.walk(repo):
        folders[:] = [name for name in folders if name != ".git"]
        for name in names:
            path = os.path.join(top, name)
            relative = os.path.relpath(path, repo)
            try:
                with open(path, encoding="utf-8", errors="replace") as handle:
                    for number, line in enumerate(handle, 1):
                        for rule, pattern in rules.items():
                            if pattern.search(line):
                                if allow_comments and "gitleaks:allow" in line:
                                    continue
                                if f"{relative}:{rule}:{number}" in ignored:
                                    continue
                                findings.append({"RuleID": rule, "File": relative, "StartLine": number, "EndLine": number,
                                                 "Secret": "REDACTED", "Match": "REDACTED", "Commit": "0" * 40})
            except OSError:
                pass
report = option("--report-path")
target = host_of(report, held) if report else None
if target:
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(findings, handle)
sys.exit(1 if findings and option("--exit-code") != "0" else 0)
'''


class Call(NamedTuple):
    """One call of the fake: its arguments (without `docker`), the folder it ran in and the names of its variables."""
    argv: List[str]
    cwd: str
    env: List[str]


class FakeDocker:
    """A folder with a `docker` in it. `FakeDocker(case)` puts it first on the PATH until the test ends (`case` is a
    unittest.TestCase: its cleanups remove the folder and put the PATH back)."""

    def __init__(self, case, on_path: bool = True):
        self.folder = Path(tempfile.mkdtemp(prefix="gravitee-lab-fakedocker-")).resolve()
        case.addCleanup(shutil.rmtree, self.folder, True)
        self.bin = self.folder / "bin"
        self.bin.mkdir()
        (self.folder / "fake_docker.py").write_text(SCRIPT, encoding="utf-8")
        wrapper = self.bin / "docker"
        wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{self.folder}/fake_docker.py" "$@"\n', encoding="utf-8")
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        self.path = str(wrapper)
        self._rules: List[dict] = []
        self._save()
        (self.folder / "real.json").write_text(json.dumps(shutil.which("docker")), encoding="utf-8")
        if on_path:
            patcher = mock.patch.dict(os.environ, {"PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}"})
            patcher.start()
            case.addCleanup(patcher.stop)

    def _save(self) -> None:
        (self.folder / "rules.json").write_text(json.dumps(self._rules), encoding="utf-8")

    def when(self, *words: str, code: int = 0, stdout: str = "", stderr: str = "", times: Optional[int] = None,
             sleep: float = 0, answers: Optional[List[dict]] = None) -> "FakeDocker":
        """A call that holds all of these words (an argument as it is, or `~text` for a text in the whole line) answers like
        this. `times`: the rule answers that many calls and no more. `answers`: a list of {code, stdout, stderr, sleep}, one
        for each matching call, the last one for the rest."""
        self._rules.append({"words": list(words), "code": code, "stdout": stdout, "stderr": stderr, "times": times,
                            "sleep": sleep, "answers": answers or []})
        self._save()
        return self

    def calls(self) -> List[Call]:
        log = self.folder / "calls.jsonl"
        if not log.exists():
            return []
        found = []
        for line in log.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            found.append(Call(row["argv"], row["cwd"], row["env"]))
        return found

    def commands(self, *words: str) -> List[List[str]]:
        """The arguments of the calls that hold all these words."""
        return [call.argv for call in self.calls() if all(word in call.argv for word in words)]

    def reset(self) -> None:
        """Forget the rules as well as the calls."""
        self._rules.clear()
        self._save()
        self.clear()

    def clear(self) -> None:
        """Forget the calls so far and how many each rule has answered."""
        for name in ("calls.jsonl", "state.json"):
            try:
                (self.folder / name).unlink()
            except FileNotFoundError:
                pass


def option(argv: List[str], name: str) -> Optional[str]:
    """The word after `name` in a command's arguments, or None."""
    return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else None


def options(argv: List[str], name: str) -> List[str]:
    """Every word after an `name` in a command's arguments."""
    return [argv[number + 1] for number, word in enumerate(argv[:-1]) if word == name]
