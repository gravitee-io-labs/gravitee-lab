"""What the seeder's tests share, as a catalog imports it (`from testkit import Seed`, with this folder on `sys.path`). The kit is
`lab_seeder/testkit.py`; this file keeps the path a catalog reads it at."""

from lab_seeder.testkit import *  # noqa: F401,F403 - the kit, under the name a catalog imports it by
