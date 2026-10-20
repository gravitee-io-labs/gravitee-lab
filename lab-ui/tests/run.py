#!/usr/bin/env python3
"""Runs the Lab UI's tests in parallel: one process for each test module, longest first, as many at a time as the machine has
cores to spare (at most six). Each module is independent of the others, which the shards of the CI prove. What a failing module
printed is printed in full.

    python3 lab-ui/tests/run.py [-j N] [--shard I/N] [module ...]     (the modules are tests.test_x; none means all of lab-ui/tests)
    python3 lab-ui/tests/run.py --time                                 (runs them all and writes the seconds each took)

The shards of the CI are balanced on the seconds each module took (durations.json, which --time writes): a share that waits for one
slow module is what makes the CI slow. A module that is not in the file counts as the median, so a new one needs no step.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent   # lab-ui/tests
LAB_UI = HERE.parent
DURATIONS = HERE / "durations.json"


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
    parser.add_argument("names", nargs="*")
    args = parser.parse_args(argv)
    names = args.names or modules()
    if args.shard:
        index, count = (int(part) for part in args.shard.split("/"))
        if not 0 <= index < count:
            parser.error("--shard I/N needs 0 <= I < N")
        names = shard(names, index, count)
    jobs = max(1, min(args.jobs, len(names)))
    took = {}

    def run(name):
        start = time.monotonic()
        done = subprocess.run([sys.executable, "-m", "unittest", name], cwd=LAB_UI, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              stdin=subprocess.DEVNULL, text=True, errors="replace")
        took[name] = round(time.monotonic() - start, 1)
        return name, done.returncode, done.stdout

    failed, count = 0, 0
    with ThreadPoolExecutor(jobs) as pool:   # the modules, largest first, as each worker is free
        for name, code, text in pool.map(run, names):
            ran = [line for line in text.splitlines() if line.startswith("Ran ")]
            count += int(ran[-1].split()[1]) if ran else 0
            if code:
                failed += 1
                print(f"== {name}\n{text}")
    print(f"Ran {count} tests in {len(names)} modules,", "FAILED" if failed else "OK", f"({jobs} processes)")
    if args.time:
        DURATIONS.write_text(json.dumps(dict(sorted(took.items())), indent=1) + "\n", encoding="utf-8")
        print(f"Wrote the seconds of {len(took)} modules to {DURATIONS.relative_to(LAB_UI.parent)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
