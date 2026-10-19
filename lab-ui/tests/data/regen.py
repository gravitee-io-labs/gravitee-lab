#!/usr/bin/env python3
"""Writes the golden files of the trust policy, and loads them for the tests.

    python3 lab-ui/tests/data/regen.py            # captures tests/data/trust/*.json from catalogs/hostile
    python3 lab-ui/tests/data/regen.py --expected # rewrites tests/data/expected-hostile.json from the verdicts

The capture needs the Docker CLI with Compose 5 and no daemon: `docker compose config` only reads files. It builds a
scratch project (the engine's core file, the hostile catalog and a stand-in for the partner catalog it includes, in the
layout of the catalogs), asks Compose for the resolved JSON of each unit exactly as the Lab does (the unit included by a
generated file, with the core), and keeps what the policy reads of it: the unit's own and overlaid services and the
top-level volumes, networks, secrets and configs that it declares. The core's golden is its configuration whole. The home folder of that run is the fake /home/hostile, so that `${HOME}` costs nothing and names no one. The
scratch folder is written as @ROOT@ and put back by `load`. The tests never run the capture: they judge the files it wrote.

Review the diff of a capture: a golden changes when Compose changes. `--expected` writes what judge says of each unit as the
oracle of the hostile catalog; it is a draft to read line by line against the trust policy, never a way to make a test pass."""

import argparse
import functools
import hashlib
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent   # lab-ui/tests/data
LAB_UI = HERE.parents[1]
ENGINE = LAB_UI.parent
CATALOG = HERE / "catalogs" / "hostile"
SUPPORT = HERE / "trust" / "support"   # the stand-in for the catalog the hostile units include
GOLDEN = HERE / "trust"
EXPECTED = HERE / "expected-hostile.json"
NS = "hostile"
FAKE_HOME = "/home/hostile"
KINDS = {"addons": "addon", "setups": "setup", "playgrounds": "playground"}
NEVER = ("t19-absolute-include", "t19-env-file-dev-zero", "seed")   # the text screen refuses them: Compose never reads them
SEEDER_IMAGES = ["example.invalid/hostile/seeder"]   # what a test says the core names as the seeder's image


def _lab():
    if str(LAB_UI) not in sys.path:
        sys.path.insert(0, str(LAB_UI))
    from lab_ui.catalogs import resolve, trust
    return resolve, trust


# ---------------------------------------------------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------------------------------------------------

def _environment():
    """The environment of every Compose run here: the path to find docker, a home that names no one, and Docker's own
    configuration folder. Nothing else of the shell it runs in: a license key, a token or any variable a unit reads would be
    interpolated into a golden, and a golden changes with the machine that captured it."""
    return {"PATH": os.environ.get("PATH", ""), "HOME": FAKE_HOME,
            "DOCKER_CONFIG": os.environ.get("DOCKER_CONFIG") or os.path.expanduser("~/.docker")}


def _compose(root, *args):
    done = subprocess.run(["docker", "compose", "--progress", "plain", *args], cwd=root, env=_environment(),
                          capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    return done.returncode, done.stdout, done.stderr


def _scratch(base):
    root = Path(base).resolve()
    shutil.copy(ENGINE / "docker-compose.yml", root / "docker-compose.yml")
    shutil.copytree(CATALOG, root / ".lab" / "catalogs" / NS)
    shutil.copytree(SUPPORT / "partner", root / ".lab" / "catalogs" / "partner")
    for folder in ("seeder", "plugins/apim", "plugins/am"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    return root


def _last(error, root):
    lines = [line.strip() for line in error.splitlines() if line.strip()]
    return (lines[-1] if lines else "docker compose failed").replace(str(root), "@ROOT@")


def _resolved(root, base, file):
    """The unit with the core as the Lab asks for it: (config or None, the last line of the error)."""
    include = Path(base) / f"all-{hashlib.sha1(str(file).encode()).hexdigest()[:10]}.yml"
    include.write_text("include:\n  - " + json.dumps(str(file)) + "\n", encoding="utf-8")
    code, out, error = _compose(root, "-f", "docker-compose.yml", "-f", str(include), "config", "--format", "json")
    return (json.loads(out), None) if code == 0 else (None, _last(error, root))


def _raw(root, file):
    """The unit as written, with what it includes merged in and nothing interpolated, labels and environment as mappings
    (what compose.raw gives)."""
    code, out, error = _compose(root, "-f", str(file), "config", "--no-interpolate", "--no-consistency", "--format", "json")
    if code:
        return None, _last(error, root)
    config = json.loads(out)
    for service in (config.get("services") or {}).values():
        for key in ("labels", "environment"):
            if isinstance(service.get(key), list):
                service[key] = dict(item.partition("=")[::2] for item in service[key])
    return config, None


def _includes(text):
    """The paths of the `include` list of a hostile file, as written."""
    block = re.search(r"^include:[ \t]*\n((?:[ \t]+-.*\n?)+)", text, re.M)
    return re.findall(r"-\s*([^\s#]+)", block.group(1)) if block else []


def _project_relative(folder, written):
    """The project-relative target of an include, None when it leaves the project."""
    target = posixpath.normpath(posixpath.join(folder, written))
    return None if target.startswith("..") or target.startswith("/") else target


def _folder(unit):
    kind, name = unit
    return f".lab/catalogs/{NS}/{kind}s/{name}" if kind != "setup" else f".lab/catalogs/{NS}/setups"


def _file(unit):
    kind, name = unit
    return f"{_folder(unit)}/compose.yml" if kind != "setup" else f".lab/catalogs/{NS}/setups/{name}.yml"


def _units():
    """(kind, name) of every unit of the hostile catalog. A golden is named by the unit, so a name is used once."""
    found = []
    for folder, kind in KINDS.items():
        for entry in sorted((CATALOG / folder).iterdir()):
            found.append((kind, entry.name[:-4] if kind == "setup" and entry.suffix == ".yml" else entry.name))
    twice = sorted(name for _, name in found if [other for _, other in found].count(name) > 1)
    if twice:
        raise SystemExit(f"a unit name is used by two kinds: {', '.join(dict.fromkeys(twice))}")
    return found


def _services(config):
    return set((config or {}).get("services") or {})


def _walk(value, old, new):
    """The same JSON with `old` replaced by `new` in every text."""
    if isinstance(value, str):
        return value.replace(old, new)
    if isinstance(value, list):
        return [_walk(item, old, new) for item in value]
    if isinstance(value, dict):
        return {key: _walk(item, old, new) for key, item in value.items()}
    return value


def _clean(value, root):
    """The JSON with the scratch folder written as @ROOT@."""
    return _walk(value, str(root), "@ROOT@")


def _trim(config, core, own, overlaid):
    """What the policy reads of a resolved configuration and nothing of the platform's own: the services the unit defines or
    overlays, and of the top-level data what the unit declares."""
    found = {}
    for key, value in config.items():
        if key.startswith("x-") or key == "services":
            continue
        if key in ("volumes", "networks", "secrets", "configs") and isinstance(value, dict):
            theirs = core.get(key) or {}
            value = {name: entry for name, entry in value.items() if theirs.get(name) != entry}
        found[key] = value
    found["services"] = {}
    for name, service in (config.get("services") or {}).items():
        if name in own or name in overlaid:
            found["services"][name] = service
    return found


def _capture_core(root, base):
    code, out, error = _compose(root, "-f", "docker-compose.yml", "config", "--format", "json")
    if code:
        raise SystemExit(f"the core does not resolve: {error.strip()}")
    core = json.loads(out)
    text = (ENGINE / "docker-compose.yml").read_text(encoding="utf-8")
    _, trust = _lab()
    installed = _compose(root, "version", "--short")[1].strip()
    license_variable = re.search(r"gravitee_license_key:\s*\$\{(\w+)", text)
    config = {key: value for key, value in core.items() if not key.startswith("x-")}
    return dict(config, _capture={"compose": installed, "note": "written by tests/data/regen.py"}, variables=trust.reads([text]),
                license_variable=license_variable.group(1) if license_variable else None), core


def _capture(unit, root, base, core):
    resolve, trust = _lab()
    kind, name = unit
    file, folder = _file(unit), _folder(unit)
    text = (root / file).read_text(encoding="utf-8")
    seed = root / folder / "seed"
    seed_texts = [path.read_text(encoding="utf-8") for path in sorted(seed.rglob("*")) if path.is_file()] \
        if kind == "playground" and seed.is_dir() else []
    targets = [_project_relative(posixpath.dirname(file), written) for written in _includes(text)]
    gold = {"unit": {"kind": kind, "id": f"@{NS}/{name}", "name": name, "meta": {}, "references": [],
                     "seed_references": [], "includes": targets, "seed": None},
            "config": None, "own": [], "overlaid": {}, "error": None, "files": {posixpath.basename(file): text}}
    if name in NEVER:
        gold["never_compose"] = True
        gold["unit"]["name"] = re.search(r"name: ([^,]+),", text).group(1)
        return gold
    config, error = _resolved(root, base, root / file)
    raw, raw_error = _raw(root, root / file)
    if raw is not None:
        gold["unit"]["meta"] = raw.get("x-gravitee") or {}
        gold["unit"]["name"] = str(gold["unit"]["meta"].get("name") or name)
        gold["unit"]["references"] = trust.reads([text], raw)
    gold["unit"]["seed_references"] = trust.seed_reads(seed_texts)
    if config is None:
        gold["error"] = error or raw_error
        return gold
    core_names = _services(core)
    included = [_resolved(root, base, root / target)[0] for target in targets if target]
    theirs = set().union(*[_services(other) for other in included]) if included else set()
    own = sorted(_services(config) - core_names - theirs)
    raws = [_raw(root, root / target)[0] for target in targets if target]
    overlaid = trust.overlays(raw, core_names, [other for other in raws if other]) if raw else {}
    gold["own"], gold["overlaid"] = own, overlaid
    gold["config"] = _trim(config, core, set(own), overlaid)
    return gold


def fresh(only=None):
    """The goldens as Compose gives them now: {name: golden, "core": the core's}. `only`: the units to capture, else all."""
    found = {}
    with tempfile.TemporaryDirectory() as holder:
        base = Path(holder)
        (base / "project").mkdir()
        root = _scratch(base / "project")
        core_gold, core = _capture_core(root, base)
        found["core"] = _clean(core_gold, root)
        for unit in _units():
            if only is None or unit[1] in only:
                found[unit[1]] = _clean(_capture(unit, root, base, core), root)
    return found


def version():
    """The Compose that is installed, '' when there is none."""
    try:
        done = subprocess.run(["docker", "compose", "version", "--short"], capture_output=True, text=True, timeout=30,
                              env=_environment(), stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def capture():
    GOLDEN.mkdir(exist_ok=True)
    for name, gold in fresh().items():
        (GOLDEN / f"{name}.json").write_text(json.dumps(gold, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if name != "core":
            print(name, "unreadable" if gold["config"] is None else "ok")


# ---------------------------------------------------------------------------------------------------------------------
# Load: what the tests judge
# ---------------------------------------------------------------------------------------------------------------------

def _put_back(value, root):
    return _walk(value, "@ROOT@", str(root))


@functools.lru_cache(maxsize=None)
def _text(name):
    return (GOLDEN / f"{name}.json").read_text(encoding="utf-8")


def load(name):
    """A golden file as written (the scratch folder still @ROOT@). A copy of its own to change."""
    return json.loads(_text(name))


def names():
    """The golden unit names, in the order of the catalog's folders."""
    return sorted(path.stem for path in GOLDEN.glob("*.json") if path.stem != "core")


def _closure(name, seen=None):
    """The (kind, name) of what a golden unit includes in the hostile catalog, itself and theirs."""
    resolve, _ = _lab()
    seen = seen if seen is not None else []
    for target in load(name)["unit"]["includes"]:
        found = resolve.target_of(target)
        if found and found[0] == NS and (found[1], found[2]) not in seen:
            seen.append((found[1], found[2]))
            _closure(found[2], seen)
    return seen


def case(name, root, listed=False, allowed=False, realpath=os.path.realpath, **more):
    """The arguments of trust.judge for a golden unit, with the project at `root` (a real folder, resolved): {unit,
    unit_config, core_config, own, overlaid, roots, env, context}. `listed` and `allowed` set host_ok as a listed or an allowed
    catalog would; `more` overrides entries of the context."""
    _, trust = _lab()
    root = Path(root).resolve()
    gold, core = _put_back(load(name), root), _put_back(load("core"), root)
    unit = gold["unit"]
    folder = root / _folder((unit["kind"], name)) if unit["kind"] != "setup" else None
    brought = _closure(name)
    folders = [root / _folder(item) for item in brought if item[0] != "setup"]
    roots = trust.build_roots(root, folder, folders, ("apim", "am"), realpath=realpath)
    services = set()
    for kind, other in brought:
        services.update(load(other)["own"])
    level = ((unit["meta"].get("requires") or {}).get("lab")) or 1
    context = {"ns": NS, "host_ok": listed or allowed, "listed": listed, "shared_settings": ["FIXTURE_SHARED_TOKEN"],
               "core_variables": core["variables"], "license_variable": core["license_variable"],
               "closure_names": [other for _, other in brought], "closure_services": sorted(services),
               "seeder_images": SEEDER_IMAGES, "requires": ["partner"], "level": level, "contract": 1, "contract_min": 1,
               "realpath": realpath, "home": FAKE_HOME}
    if gold["error"]:
        context["error"] = gold["error"]
    context.update(more)
    return {"unit": unit, "unit_config": gold["config"],
            "core_config": {key: value for key, value in core.items() if key not in ("_capture", "variables", "license_variable")},
            "own": gold["own"], "overlaid": gold["overlaid"], "roots": roots, "env": set(), "context": context}


def judged(name, root, **more):
    """trust.judge on a golden unit; a unit the text screen refuses is judged as the Lab judges it, from the screen."""
    _, trust = _lab()
    arguments = case(name, root, **more)
    if load(name).get("never_compose"):
        text = load(name)["files"]["compose.yml"]
        arguments["context"]["screened"] = trust.prescreen(text, f"{_file(('addon', name))}")
    return trust.judge(**arguments)


def _key(name):
    return load(name)["unit"]["kind"], name


def _same(path):
    return path


def pipeline(root, listed=False, realpath=_same, **more):
    """What the Lab makes of the hostile catalog, step by step as project.build does (6.3): every unit judged alone, the names
    claimed in the order of 6.5 after an earlier unit of the default catalog that holds the service `fixture-echo` (the
    reason `t21-name-clash` is blocked), the audience (the hostile catalog is public, the partner catalog private: T24), and
    each verdict combined with those of the units it brings (T25). {name: the verdict, plus `closure`}."""
    resolve, trust = _lab()
    core = load("core")
    verdicts = {name: judged(name, root, listed=listed, realpath=realpath, **more) for name in names()}
    includes = {_key(name): [] for name in names()}
    for name in names():
        for target in load(name)["unit"]["includes"]:
            found = resolve.target_of(target)
            if found and found[0] == NS:
                includes[_key(name)].append((found[1], found[2]))
    earlier = ("addon", "echo")
    owned = {earlier: {"name": "Echo", "tier": "default", "services": ["fixture-echo"], "containers": [], "volumes": []}}
    for name in names():
        gold = load(name)
        config, own = gold["config"], gold["own"]
        if config:
            owned[_key(name)] = dict(resolve.owned_names(config, core, own), name=gold["unit"]["name"], tier="other",
                                     kind=gold["unit"]["kind"], id=gold["unit"]["id"])
    closures = {key: resolve.closure(includes, *key) for key in includes}
    order = resolve.precedence_order({"fixture": [earlier], NS: list(includes)}, ["fixture", NS])
    clashes = resolve.collisions(order, owned, closures, [_key(n) for n in names() if verdicts[n]["state"] == "blocked"])
    found = {}
    for name in names():
        verdict = verdicts[name]
        if _key(name) in clashes:
            verdict = trust.add_reason(verdict, "T21", clashes[_key(name)])
        if any(target and resolve.target_of(target) and resolve.target_of(target)[0] == "partner"
               for member in closures[_key(name)] for target in (load(member[1])["unit"]["includes"] if member[1] in names() else [])):
            if resolve.audience_blocked(resolve.rank("public"), resolve.rank("private")):
                verdict = trust.add_reason(verdict, "T24", trust.line("T24"))
        found[name] = verdict
    for name in names():
        brought = [found[member[1]] for member in closures[_key(name)][1:] if member[1] in found]
        found[name] = dict(trust.combine([found[name], *brought]), closure=closures[_key(name)])
    return found


def expected(root):
    """The oracle draft: what the Lab makes of every unit of the hostile catalog (pipeline), and of the same unit when its
    catalog is listed (only when that differs)."""
    found, again = pipeline(root), pipeline(root, listed=True)
    result = {}
    for name in names():
        verdict = found[name]
        entry = {"verdict": verdict["state"], "reasons": verdict["reasons"], "notes": verdict["notes"],
                 "surface": verdict["surface"], "closure": [f"{kind}:{other}" for kind, other in verdict["closure"]],
                 "ports": verdict["surface"]["ports"]}
        if again[name]["reasons"] != verdict["reasons"]:
            entry["when_listed"] = again[name]["reasons"]
        result[name] = entry
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--expected", action="store_true", help="rewrite expected-hostile.json from the verdicts")
    if parser.parse_args().expected:
        with tempfile.TemporaryDirectory() as holder:
            EXPECTED.write_text(json.dumps(expected(holder), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    else:
        if not shutil.which("docker"):
            raise SystemExit("regen needs the Docker CLI with Compose 5")
        capture()


if __name__ == "__main__":
    main()
