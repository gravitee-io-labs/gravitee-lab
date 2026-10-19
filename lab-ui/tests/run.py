#!/usr/bin/env python3
"""Runs the Lab UI's tests in parallel: one process for each test module, largest first, as many at a time as the machine has
cores to spare (at most six). Each module is independent of the others, which the shards of the CI prove. What a failing module
printed is printed in full.

    python3 lab-ui/tests/run.py [-j N] [--shard I/N] [module ...]     (the modules are tests.test_x; none means all of lab-ui/tests)
"""

import argparse
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent   # lab-ui/tests
LAB_UI = HERE.parent


def modules():
    files = sorted(HERE.rglob("test_*.py"), key=lambda path: (-path.stat().st_size, path.name))
    return [".".join(("tests", *path.relative_to(HERE).with_suffix("").parts)) for path in files]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("-j", "--jobs", type=int, default=min(6, max(2, (os.cpu_count() or 4) // 2)))
    parser.add_argument("--shard", metavar="I/N", help="only the I-th of N shares of the modules (0 <= I < N), as the CI runs them")
    parser.add_argument("names", nargs="*")
    args = parser.parse_args(argv)
    names = args.names or modules()
    if args.shard:
        index, count = (int(part) for part in args.shard.split("/"))
        names = names[index::count]
    jobs = max(1, min(args.jobs, len(names)))

    def run(name):
        done = subprocess.run([sys.executable, "-m", "unittest", name], cwd=LAB_UI, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              stdin=subprocess.DEVNULL, text=True, errors="replace")
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
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
