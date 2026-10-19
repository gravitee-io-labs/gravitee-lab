"""Gravitee Lab UI, on http://localhost:9000. Standard library only; the code is in lab_ui/.

    make ui        # or: python3 lab-ui/server.py [--port 9000] [--no-open] [--check] [--list]
"""

import sys
from pathlib import Path

if __name__ == "__main__":
    if sys.version_info < (3, 9):   # said here: an older Python may not even load the Lab UI
        sys.exit("Python 3.9 or later is needed: this is " + sys.version.split()[0])
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from lab_ui.server.api import main
    main()
