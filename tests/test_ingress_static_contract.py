import ast
import unittest
from pathlib import Path


class IngressStaticContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = Path("toolforge_queue_api.py")
        cls.source = cls.path.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_no_unconditional_redis_import(self):
        self.assertNotIn("import redis", self.source)

    def test_settings_has_no_duplicate_fields(self):
        settings_class = next(
            node for node in self.tree.body if isinstance(node, ast.ClassDef) and node.name == "Settings"
        )
        names = []
        for node in settings_class.body:
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names.append(node.target.id)

        self.assertEqual(len(names), len(set(names)))


if __name__ == "__main__":
    unittest.main()
