"""The engine holds no item. The add-ons, setups and playgrounds are modules that catalogs bring; nothing the Lab is made of
mentions one of them or rests on what it does. Code, tests, documents and fixtures use invented names (Acme Shop, kiosk, jade),
so that a change to an item can never be a change to the Lab.

The check reads every tracked file for the ids of the items of the catalogs the Lab ships with. When a catalog adds an item
whose name is not a common word, add its id here."""

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# The ids and names of the items of the default catalog and of the internal one, written as a pattern. Products and
# technologies that the Lab itself works with (Kafka, Kubernetes, Redis, Ollama, OpenFGA, Solace) are not here: they are
# words of the platform's world, and the seeder's sections for some of them are part of the contract.
ITEMS = re.compile(
    r"acme[-_ ]?(bank|hotels?|logistics)|apexora|agent[-_ ]inspector|agent[-_ ]tool[-_ ]layer|ai[-_ ]agents?[-_ ]inspector"
    r"|event[-_ ]management|federation[-_](apigee|aws|solace|controller)|gamma[-_ ]tracing|headlamp|jaeger|kafka[-_ ]console"
    r"|kibana|kubernetes[-_ ]automation|mcp[-_ ]inspector|mcpjam|ollama[-_ ]admin|redisinsight|bank[-_](web|api|mcp|agent|traffic|seed)",
    re.I)
ALLOWED = {
    ".github/tests/test_engine_holds_no_item.py",   # this list
    "catalogs.default.json",                        # the address of the default catalog
}
TEXT = {".py", ".js", ".mjs", ".md", ".yml", ".yaml", ".json", ".mk", ".txt", ".toml", ".css", ".html", ".sh", ""}


class Engine(unittest.TestCase):
    def test_no_tracked_file_names_an_item_of_a_catalog(self):
        tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, text=True, check=True)
        found = []
        for name in filter(None, tracked.stdout.split("\0")):
            path = ROOT / name
            if name in ALLOWED or not path.is_file() or path.suffix not in TEXT:
                continue
            for number, line in enumerate((name + "\n" + path.read_text(encoding="utf-8", errors="replace")).splitlines()):
                match = ITEMS.search(line)
                if match:
                    found.append(f"{name}:{max(number, 1)}: {match.group(0)}")
                    break   # one finding per file is enough to know where to look
        self.assertEqual([], found, "an item of a catalog is named in the engine: use an invented name")


if __name__ == "__main__":
    unittest.main()
