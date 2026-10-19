"""The checks of the seed files of a catalog's playgrounds, as a command (`python seedlint.py PLAYGROUNDS`): the one a catalog's CI
runs. The checks are `lab_seeder/seedlint.py`; this file keeps the path a catalog reads them at."""

import sys

from lab_seeder.seedlint import *  # noqa: F401,F403 - the checks, under the name a catalog imports them by
from lab_seeder.seedlint import main

if __name__ == "__main__":
    sys.exit(main())
