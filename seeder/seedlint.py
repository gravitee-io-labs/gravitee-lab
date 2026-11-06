"""The checks of the seed files of a catalog's playgrounds, as a command (`python seedlint.py PLAYGROUNDS`): the one a catalog's CI
runs. The checks are `lab_seeder/seedlint.py`; this file keeps the path a catalog runs them at."""

import sys

from lab_seeder.seedlint import main

if __name__ == "__main__":
    sys.exit(main())
