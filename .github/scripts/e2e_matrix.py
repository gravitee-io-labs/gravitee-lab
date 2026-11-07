"""Which items the end-to-end run of CI tries, as the matrix of its jobs: one job for each item and each set of versions it was
tested with (`x-gravitee.tested`), or one on the core's defaults when it says none. The plan itself is `lab_ui/e2e.py`'s; this is
the command the workflow runs, and what it prints for the next job to read.

The engine holds no item: the items are the default catalog's, linked into the engine before this runs (`catalog.py link`).

    python3 -I .github/scripts/e2e_matrix.py --changed BASE HEAD      # its canary, when a change of the engine touches what every item shares
    python3 -I .github/scripts/e2e_matrix.py --canary                 # the items its catalog.yml names
    python3 -I .github/scripts/e2e_matrix.py --items -- KIND:ID ...   # these items

An item is `kind:id` with the id of the Lab. What comes after `--` is never an option.

Prints the matrix as JSON, {"matrix": {"include": [...]}, "count": n}. With --github-output it is also appended to the file
GITHUB_OUTPUT names, for a later job to read. An item that does not load, and a catalog that names no canary when one is needed,
are errors, so that a check with nothing to try does not pass."""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "lab-ui") not in sys.path:   # python3 -I puts no folder of the repository on the path
    sys.path.insert(0, str(ROOT / "lab-ui"))

from lab_ui import e2e   # noqa: E402


def load_project():
    from lab_ui.library import project   # the repository's own reader of its Compose files
    return project.load()


def parser():
    top = argparse.ArgumentParser(description="The matrix of the end-to-end run of CI.")
    what = top.add_mutually_exclusive_group(required=True)
    what.add_argument("--changed", nargs=2, metavar=("BASE", "HEAD"), help="the canary, when a change of the engine touches what every item shares")
    what.add_argument("--canary", action="store_true", help="the items catalog.yml names as its canary")
    what.add_argument("--items", action="store_true", help="the items after --")
    top.add_argument("--github-output", action="store_true", help="also append the result to the file GITHUB_OUTPUT names")
    return top


def read(argv):
    """The arguments of a command line. The words after `--` are the items: after it, none can be taken for an option."""
    argv = list(sys.argv[1:] if argv is None else argv)
    at = argv.index("--") if "--" in argv else len(argv)
    top = parser()
    args = top.parse_args(argv[:at])
    args.words = argv[at + 1:]
    if args.items != bool(args.words):
        top.error("--items takes its items after --, and nothing else takes words")
    return args


def main(argv=None):
    args = read(argv)
    try:
        found = e2e.plan(load_project(), changed=tuple(args.changed) if args.changed else None, words=args.words or None)
    except e2e.PlanError as error:
        sys.exit(str(error))
    except (e2e.compose.ComposeError, OSError) as error:   # the project could not be read: no matrix is a failure, not an empty one
        sys.exit(f"The project cannot be read: {' '.join(str(error).split())[:300]}")
    print(json.dumps(found, indent=2))
    if args.github_output:
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as file:
            file.write(f"matrix={json.dumps(found['matrix'])}\ncount={found['count']}\n")


if __name__ == "__main__":
    main()
