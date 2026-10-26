#!/usr/bin/env python3
"""Runs the Lab UI's tests in parallel: one process for each test module, longest first, as many at a time as the machine has
cores to spare (at most six). Each module is independent of the others, which the shards of the CI prove. What a failing module
printed is printed in full.

    python3 lab-ui/tests/run.py [-j N] [--shard I/N] [module ...]     (the modules are tests.test_x; none means all of lab-ui/tests)
    python3 lab-ui/tests/run.py --time                                 (runs them all and writes the seconds each took)
    python3 lab-ui/tests/run.py --quick                                (only the modules that take under QUICK seconds)
    python3 lab-ui/tests/run.py --imports                              (imports every module of the Lab UI, runs no test)

The shards of the CI are balanced on the seconds each module took (durations.json, which --time writes): a share that waits for one
slow module is what makes the CI slow. A module that is not in the file counts as the median, so a new one needs no step.
The oldest Python the Lab UI supports runs a lighter lane: every module imports (a feature newer than it fails there), and the quicker
modules run.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent   # lab-ui/tests
LAB_UI = HERE.parent
DURATIONS = HERE / "durations.json"
QUICK = 15   # seconds: a module under this is one of the quicker ones (--quick)


def durations():
    """The seconds each module took, as --time wrote them; empty when the file cannot be read."""
    try:
        found = json.loads(DURATIONS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {name: float(seconds) for name, seconds in found.items() if isinstance(seconds, (int, float))} if isinstance(found, dict) else {}


def modules():
    """Every module, longest first (the median for one that is not timed yet), so that the slowest start first."""
    found = [".".join(("tests", *path.relative_to(HERE).with_suffix("").parts)) for path in sorted(HERE.rglob("test_*.py"))]
    return sorted(found, key=lambda name, weight=weights(found): (-weight[name], name))


def weights(names):
    known = durations()
    middle = sorted(known.values())[len(known) // 2] if known else 1.0
    return {name: known.get(name, middle) for name in names}


def quick(names):
    """The modules that take under QUICK seconds, and every module not timed yet: a new one runs on the oldest Python too."""
    known = durations()
    return [name for name in names if known.get(name, 0) < QUICK]


def imports():
    """Imports every module of the Lab UI in a fresh interpreter, and prints those that fail: 0 when all import."""
    found = sorted(".".join(path.relative_to(LAB_UI).with_suffix("").parts) for path in (LAB_UI / "lab_ui").rglob("*.py"))
    found = [name[: -len(".__init__")] if name.endswith(".__init__") else name for name in found]
    script = ("import importlib, sys, traceback\nbad = 0\nfor name in sys.argv[1:]:\n    try:\n        importlib.import_module(name)\n"
              "    except Exception:\n        bad += 1\n        print('== ' + name)\n        traceback.print_exc(file=sys.stdout)\n"
              "sys.exit(1 if bad else 0)")
    done = subprocess.run([sys.executable, "-c", script, *found], cwd=LAB_UI, stdin=subprocess.DEVNULL)
    print(f"Imported {len(found)} modules on Python {sys.version.split()[0]},", "FAILED" if done.returncode else "OK")
    return 1 if done.returncode else 0


def shard(names, index, count):
    """The index-th of count shares of the modules: each, longest first, goes to the share that has the least time so far."""
    weight, loads, shares = weights(names), [0.0] * count, [[] for _ in range(count)]
    for name in sorted(names, key=lambda name: (-weight[name], name)):
        least = loads.index(min(loads))
        shares[least].append(name)
        loads[least] += weight[name]
    return shares[index]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("-j", "--jobs", type=int, default=min(6, max(2, (os.cpu_count() or 4) // 2)))
    parser.add_argument("--shard", metavar="I/N", help="only the I-th of N shares of the modules (0 <= I < N), as the CI runs them")
    parser.add_argument("--time", action="store_true", help="run every module and write the seconds each took to durations.json")
    parser.add_argument("--quick", action="store_true", help=f"only the modules that take under {QUICK} seconds")
    parser.add_argument("--imports", action="store_true", help="import every module of the Lab UI, and run no test")
    parser.add_argument("names", nargs="*")
    args = parser.parse_args(argv)
    if args.imports:
        return imports()
    names = args.names or modules()
    if args.quick:
        names = quick(names)
    if args.shard:
        index, count = (int(part) for part in args.shard.split("/"))
        if not 0 <= index < count:
            parser.error("--shard I/N needs 0 <= I < N")
        names = shard(names, index, count)
    jobs = max(1, min(args.jobs, len(names)))
    took = {}
    # Each module writes its temporary files in a folder of its own, and the run removes them all at its end: a process a test
    # stops, which runs no exit handler, leaves nothing behind in the system's temporary folder
    temporary = tempfile.mkdtemp(prefix="gravitee-lab-tests-")

    def run(name):
        start = time.monotonic()
        mine = os.path.join(temporary, name)
        os.mkdir(mine)
        done = subprocess.run([sys.executable, "-m", "unittest", name], cwd=LAB_UI, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              stdin=subprocess.DEVNULL, text=True, errors="replace", env={**os.environ, "TMPDIR": mine})
        took[name] = round(time.monotonic() - start, 1)
        return name, done.returncode, done.stdout

    failed, count = 0, 0
    try:
        with ThreadPoolExecutor(jobs) as pool:   # the modules, largest first, as each worker is free
            for name, code, text in pool.map(run, names):
                ran = [line for line in text.splitlines() if line.startswith("Ran ")]
                count += int(ran[-1].split()[1]) if ran else 0
                if code:
                    failed += 1
                    print(f"== {name}\n{text}")
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    print(f"Ran {count} tests in {len(names)} modules,", "FAILED" if failed else "OK", f"({jobs} processes)")
    if args.time:
        DURATIONS.write_text(json.dumps(dict(sorted(took.items())), indent=1) + "\n", encoding="utf-8")
        print(f"Wrote the seconds of {len(took)} modules to {DURATIONS.relative_to(LAB_UI.parent)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
