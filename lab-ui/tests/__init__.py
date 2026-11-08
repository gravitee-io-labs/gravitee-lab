"""The Lab UI's unit tests. `Case` is what their test cases share: what a test replaces is put back, and the
project folder it writes is deleted, once it ends."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lab_ui.catalogs.registry import CatalogError

# The tests are the same on a laptop and on a runner: what GitHub Actions puts in the environment (the actor, the repository, the
# marker that makes a tool print workflow commands) is not theirs, and a test that wants one sets it.
for _name in [name for name in os.environ if name.startswith(("GITHUB_", "RUNNER_")) or name in ("CI", "ACTIONS_ID_TOKEN_REQUEST_URL")]:
    del os.environ[_name]


class Case(unittest.TestCase):

    def start(self, patcher):
        """A patcher started until the test ends: answers what it put in place."""
        self.addCleanup(patcher.stop)
        return patcher.start()

    def patch(self, target, name, *new, **how):
        """target.name replaced until the test ends, by `new`, else by a mock made with `how`: answers it."""
        return self.start(mock.patch.object(target, name, *new, **how))

    def folder(self, *modules, files=None):
        """A project folder of the test's own, `self.root`: the ROOT of these modules, holding `files` ({path:
        text}). Answers its path."""
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.root = Path(holder.name).resolve()
        for module in modules:
            self.patch(module, "ROOT", self.root)
        for path, text in (files or {}).items():
            self.write(path, text)
        return self.root

    def refusal(self, call, code=None):
        """The CatalogError that `call()` raises, of this code when one is given."""
        with self.assertRaises(CatalogError) as raised:
            call()
        if code:
            self.assertEqual(raised.exception.code, code)
        return raised.exception

    def write(self, path, text=""):
        """A file of the folder written, with the folders it is in: answers its path."""
        file = self.root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text, encoding="utf-8")
        return path

    @staticmethod
    def started(jobs):
        """The job a mocked JOBS was last asked to start: (title, steps, its other arguments)."""
        (title, steps), more = jobs.start.call_args
        return title, steps, more
