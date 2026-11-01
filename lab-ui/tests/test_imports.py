"""Every import of the Lab UI resolves, the ones a function makes late included: a module a test stands in for is a module that
may not exist, and nothing else would say so until a person pressed the button that needs it. And the engine imports one way:
paths and products, then docker, then the library and gravitee, then the server, never in a cycle."""

import ast
import importlib
import pkgutil
import unittest

import lab_ui

# A stage of the standard that a later level ships: standard._reusable reads its absence and says so
LATER = {("lab_ui.catalogs.standard", "workflows")}

# The packages of the engine, in the order they import each other. The catalogs (catalogs/) and the commands at the root (e2e.py,
# compat.py) are not in it
ENGINE = ("lab_ui.paths", "lab_ui.products", "lab_ui.docker", "lab_ui.library", "lab_ui.gravitee", "lab_ui.server")
# What a module of the engine imports in a function, each for its reason: any other is a cycle being hidden, or a module that
# belongs lower. An import by name (importlib.import_module, sys.modules) is one too
LATE = {
    ("lab_ui.paths", "lab_ui.catalogs.registry"): "a git catalog lists the units its manifest vouches for, which the registry "
                                                  "reads, and the registry reads paths",
    ("lab_ui.library.layers", "lab_ui.catalogs.service"): "the preflight of a start that turns on a unit of a catalog, which "
                                                          "reads layers",
    ("lab_ui.server.api", "lab_ui.e2e"): "the command line's --e2e, which runs this server",
}


def edges():
    """[(module, the module of the Lab it imports, late)] of every module of the engine: an import at the top of the file, or
    in a function (late), the names it imports from a package as the modules they are."""
    modules = {"lab_ui", *(info.name for info in pkgutil.walk_packages(lab_ui.__path__, "lab_ui."))}
    _module = modules.__contains__
    found = []
    for name in sorted(modules):
        if not name.startswith(ENGINE):
            continue
        module = importlib.import_module(name)
        package = name if hasattr(module, "__path__") else name.rpartition(".")[0]
        with open(module.__file__, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())

        def walk(node, late):
            for child in ast.iter_child_nodes(node):
                inner = late or isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
                if isinstance(child, ast.ImportFrom):
                    base = (".".join(package.split(".")[:len(package.split(".")) - (child.level - 1)]
                                     + ([child.module] if child.module else [])) if child.level else child.module or "")
                    if base.startswith("lab_ui"):
                        found.extend((name, f"{base}.{each.name}" if _module(f"{base}.{each.name}") else base, late)
                                     for each in child.names)
                elif isinstance(child, ast.Import):
                    found.extend((name, each.name, late) for each in child.names if each.name.startswith("lab_ui"))
                elif isinstance(child, ast.Constant) and isinstance(child.value, str) and child.value.startswith("lab_ui.") \
                        and _module(child.value) and not child.value.startswith(name):
                    found.append((name, child.value, True))   # a module looked up by its name
                walk(child, inner)
        walk(tree, False)
    return found


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


class OneWay(unittest.TestCase):
    """The engine imports one way: a function that imports what imports its module hides a cycle, which fails only when that
    function runs, and docker/ is read and tested without the library."""

    def test_the_lowest_import_nothing_above_them(self):
        # paths and products import nothing of the Lab; docker/ is what the Lab asks of Docker, and imports only those
        rank = {"lab_ui.paths": 0, "lab_ui.products": 0, "lab_ui.docker": 1}
        found = [f"{module} imports {target}" for module, target, _ in edges()
                 if (module, target) not in LATE
                 for top in [next((one for one in rank if module.startswith(one)), None)] if top is not None
                 and not any(target.startswith(one) and rank[one] <= rank[top] for one in rank)]
        self.assertEqual(found, [])

    def test_the_library_and_gravitee_never_import_the_server(self):
        found = [f"{module} imports {target}" for module, target, _ in edges()
                 if module.startswith(("lab_ui.library", "lab_ui.gravitee")) and target.startswith(("lab_ui.server", "lab_ui.e2e"))]
        self.assertEqual(found, [])

    def test_a_function_imports_a_module_of_the_lab_only_for_a_reason_said_here(self):
        found = sorted({f"{module} imports {target} in a function" for module, target, late in edges()
                        if late and (module, target) not in LATE})
        self.assertEqual(found, [])

    def test_no_module_imports_one_that_imports_it(self):
        graph = {}
        for module, target, _ in edges():
            if target.startswith(ENGINE) and module != target:
                graph.setdefault(module, set()).add(target)
        cycles, state = [], {}

        def visit(node, path):
            state[node] = "open"
            for target in sorted(graph.get(node, ())):
                if state.get(target) == "open":
                    cycles.append(" -> ".join(path[path.index(target):] + [target]))
                elif target not in state:
                    visit(target, path + [target])
            state[node] = "done"
        for node in sorted(graph):
            if node not in state:
                visit(node, [node])
        self.assertEqual(cycles, [])


if __name__ == "__main__":
    unittest.main()
