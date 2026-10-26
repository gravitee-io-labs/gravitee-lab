"""The version a merge to main publishes: the one before it, raised by the labels of the pull requests merged since.

    python3 -I .github/scripts/next_version.py LAST [LABEL ...]    # LAST is the newest tag vX.Y.Z on main, empty before the first

The label `major` raises the major number (an update that needs a person to do something by hand), `minor` the minor number (a
new feature), and anything else the patch number: every merge is a version. `catalog-format` marks something catalogs can use from
this version on (docs/contract.md), which is a new feature: it raises the minor number at least. The highest label of all the pull requests wins, as
when two merges land while the release of the first is still running. With no version before it, the first is v1.0.0. Prints the
tag of the new version."""

import re
import sys

TAG = re.compile(r"v(\d+)\.(\d+)\.(\d+)")
FIRST = "v1.0.0"


def next_version(last, labels):
    """The tag after `last` (a tag vX.Y.Z, or empty), raised by the highest of `labels`."""
    if not last:
        return FIRST
    found = TAG.fullmatch(last)
    if not found:
        raise ValueError(f"{last} is not a version (vX.Y.Z)")
    major, minor, patch = (int(number) for number in found.groups())
    if "major" in labels:
        return f"v{major + 1}.0.0"
    if "minor" in labels or "catalog-format" in labels:
        return f"v{major}.{minor + 1}.0"
    return f"v{major}.{minor}.{patch + 1}"


def main(argv):
    if not argv:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    try:
        print(next_version(argv[0], set(argv[1:])))
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
