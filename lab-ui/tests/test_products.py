"""products: the names of Gravitee's products and images the engine knows itself, the one place of them in lab_ui/."""

import ast
import re
import unittest
from pathlib import Path

import lab_ui
from lab_ui import paths, products
from lab_ui.library import versions

# How the engine would name a product or an image of Gravitee in a text of its own: the names of the products, the
# gateway's image, the Azure registries and the folders of a seed
NAMED = re.compile(r"API Management|Access Management|\bGamma\b|apim-gateway|azurecr|^(?:apim|am|aim|authz|openfga|apim-)$")


class Products(unittest.TestCase):
    def test_the_systems_are_those_of_the_images_page(self):
        source = (paths.WEB_DIR / "js" / "core.js").read_text(encoding="utf-8")
        listed = re.search(r"^export const VARIANTS = \[(.*?)\];", source, re.M).group(1)
        self.assertEqual(products.VARIANTS, tuple(re.findall(r'"([^"]+)"', listed)))
        self.assertEqual(versions.bare("4.12.20-chainguard-fips"), ("4.12.20", "chainguard-fips"))

    def test_the_engine_names_them_here_and_nowhere_else(self):
        # A text of the engine (not a docstring) that names a product or one of its images is a second place to change. The
        # catalogs' standard (catalogs/) says what a catalog's repository may hold, which is not the engine's knowledge
        package, found = Path(lab_ui.__file__).parent, []
        for path in sorted(package.rglob("*.py")):
            if path == Path(products.__file__) or path.relative_to(package).parts[0] == "catalogs":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            docs = {id(node.body[0].value) for node in ast.walk(tree)
                    if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)) and node.body
                    and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)}
            found += [f"{path.relative_to(package)}:{node.lineno}: {node.value!r}" for node in ast.walk(tree)
                      if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs
                      and NAMED.search(node.value)]
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
