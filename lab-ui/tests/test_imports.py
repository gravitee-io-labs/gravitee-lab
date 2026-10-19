"""Every import of the Lab UI resolves, the ones a function makes late included: a module a test stands in for is a module that
may not exist, and nothing else would say so until a person pressed the button that needs it."""

import ast
import importlib
import pkgutil
import unittest

import lab_ui

# A stage of the standard that a later level ships: standard._reusable reads its absence and says so
LATER = {("lab_ui.catalogs.standard", "workflows")}


class Imports(unittest.TestCase):
    def test_every_name_a_module_imports_from_the_lab_is_there(self):
        names = [found.name for found in pkgutil.walk_packages(lab_ui.__path__, "lab_ui.")]
        unresolved = []
        for name in names:
            module = importlib.import_module(name)
            package = name if hasattr(module, "__path__") else name.rpartition(".")[0]
            with open(module.__file__, encoding="utf-8") as handle:
                tree = ast.parse(handle.read())
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom):
                    continue
                if node.level:
                    parts = package.split(".")
                    target = ".".join(parts[:len(parts) - (node.level - 1)] + ([node.module] if node.module else []))
                else:
                    target = node.module or ""
                if not target.startswith("lab_ui"):
                    continue
                home = importlib.import_module(target)
                for each in node.names:
                    if each.name == "*" or hasattr(home, each.name) or (name, each.name) in LATER:
                        continue
                    try:
                        importlib.import_module(f"{target}.{each.name}")
                    except ImportError:
                        unresolved.append(f"{name}:{node.lineno} imports {each.name} from {target}")
        self.assertEqual([], unresolved)


if __name__ == "__main__":
    unittest.main()
