"""Seeds a playground: the entry point of the seeder image (`python seed.py`). The seeder is `lab_seeder/`; see README.md."""

import sys

from lab_seeder.seed import main

if __name__ == "__main__":
    sys.exit(main())
