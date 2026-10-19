"""The command of the catalogs: `python3 -I lab-ui/catalog.py <command>`. The code is in lab_ui/catalogs/cli.py, which lists the commands."""

import sys
from pathlib import Path

if __name__ == "__main__":
    if sys.version_info < (3, 9):   # said here: an older Python may not even load the Lab UI
        sys.exit("Python 3.9 or later is needed: this is " + sys.version.split()[0])
    sys.path.insert(0, str(Path(__file__).resolve().parent))   # python3 -I does not put the folder of the script on the path
    from lab_ui.catalogs.cli import main
    sys.exit(main())
