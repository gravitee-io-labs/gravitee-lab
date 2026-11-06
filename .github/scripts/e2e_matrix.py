"""Which items the end-to-end run of CI tries, as the matrix of its jobs: one job for each item and each set of versions it was
tested with (`x-gravitee.tested`), or one on the core's defaults when it says none. The plan itself is `lab_ui/e2e.py`'s; this is
the command a workflow runs, and what it prints for the next job to read.

The engine holds no item: the items are a catalog's, linked into the engine before this runs (`catalog.py link`).

    python3 -I .github/scripts/e2e_matrix.py --catalog ID --changed BASE HEAD            # what a change in the engine is for it
    python3 -I .github/scripts/e2e_matrix.py --catalog ID --all                          # every shared item: the nightly run
    python3 -I .github/scripts/e2e_matrix.py --catalog ID --canary                       # the items its catalog.yml names
    python3 -I .github/scripts/e2e_matrix.py --catalog ID --items [KIND:ID ...] [-- KIND:ID ...]   # these items

`--default-catalog` is the catalog this engine lists first, in place of `--catalog ID`. An item is `kind:id` with the id of the
Lab: bare for the first catalog, `@catalog/name` for another. Words after `--` are items too, and never an option.

Prints the matrix as JSON, {"matrix": {"include": [...]}, "count": n}. With --github-output it is also appended to the file
GITHUB_OUTPUT names, for a later job to read. When what all items share changes (the core, the seeder, the Lab UI, the Makefile
and this folder), the canary of the catalog is tried. What belongs to a machine (`local/`) is never run.

An item whose files do not load is an error and is never left out as if it had gone; a catalog that names no canary is an error
for `--canary` and for a change of what all items share.

A catalog is always named (`--catalog ID` or `--default-catalog`): the engine holds no item."""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "lab-ui") not in sys.path:   # python3 -I puts no folder of the repository on the path
    sys.path.insert(0, str(ROOT / "lab-ui"))

from lab_ui import e2e   # noqa: E402

jobs = e2e.jobs


def load_project():
    from lab_ui.library import project   # the repository's own reader of its Compose files
    return project.load()


def matrix(args, project):
    """{"matrix": {"include": [job, ...]}, "count": n} for what the arguments ask of the catalog they name."""
    return e2e.plan(args.catalog or None, project, changed=tuple(args.changed) if args.changed else None,
                    everything=bool(args.all), canary_only=bool(args.canary), words=args.items)


def parser():
    top = argparse.ArgumentParser(description="The matrix of the end-to-end run of CI.")
    which = top.add_mutually_exclusive_group()
    which.add_argument("--catalog", metavar="ID", help="the catalog to try, linked into this engine")
    which.add_argument("--default-catalog", action="store_true", help="the catalog this engine lists first")
    what = top.add_mutually_exclusive_group(required=True)
    what.add_argument("--changed", nargs=2, metavar=("BASE", "HEAD"), help="the canary, when a change touches what every item shares")
    what.add_argument("--all", action="store_true", help="every shared item")
    what.add_argument("--canary", action="store_true", help="the items catalog.yml names as its canary")
    what.add_argument("--items", nargs="*", metavar="KIND:ID", help="these items")
    top.add_argument("--github-output", action="store_true", help="also append the result to the file GITHUB_OUTPUT names")
    return top


def read(argv):
    """The arguments of a command line. Words after `--` are items: they come after it so that none can be taken for an option."""
    argv = list(sys.argv[1:] if argv is None else argv)
    after = []
    if "--" in argv:
        at = argv.index("--")
        argv, after = argv[:at], argv[at + 1:]
    top = parser()
    args = top.parse_args(argv)
    if after:
        if args.items is None:
            top.error("words after -- are the items of --items")
        args.items += after
    if args.items is not None and not args.items:
        top.error("--items needs at least one item")
    if not (args.catalog or args.default_catalog):
        top.error("name the catalog: --catalog ID or --default-catalog")
    return args


def write_output(**values):
    """Appends name=value lines to the file GITHUB_OUTPUT names, for a later job to read."""
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as file:
        for name, value in values.items():
            file.write(f"{name}={value}\n")


def main(argv=None):
    args = read(argv)
    try:
        found = matrix(args, load_project())
    except e2e.PlanError as error:
        sys.exit(str(error))
    except (e2e.compose.ComposeError, OSError) as error:   # the project could not be read: no matrix is a failure, not an empty one
        sys.exit(f"The project cannot be read: {' '.join(str(error).split())[:300]}")
    print(json.dumps(found, indent=2))
    if args.github_output:
        write_output(matrix=json.dumps(found["matrix"]), count=found["count"])


if __name__ == "__main__":
    main()
