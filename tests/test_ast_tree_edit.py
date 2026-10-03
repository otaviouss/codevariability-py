import ast
import random
import tempfile
import unittest
from pathlib import Path

from codevariability import AnalysisError, analyze
from codevariability.ast_tree_edit import (
    AST_TREE_EDIT_METRIC,
    NormalizedAstNode,
    ast_tree_edit_similarity,
    count_ast_nodes,
    normalize_ast,
    normalize_python_source,
    tree_edit_distance,
    tree_edit_similarity,
)


class TreeEditPrimitiveTest(unittest.TestCase):
    def test_empty_tree_contract_and_unit_costs(self):
        leaf_a = NormalizedAstNode("A")
        leaf_b = NormalizedAstNode("B")
        parent = NormalizedAstNode("A", (leaf_b,))
        self.assertEqual(tree_edit_similarity(None, None), 1.0)
        self.assertEqual(tree_edit_similarity(None, leaf_a), 0.0)
        self.assertEqual(tree_edit_distance(leaf_a, leaf_b), 1)
        self.assertEqual(tree_edit_distance(leaf_a, parent), 1)
        self.assertEqual(count_ast_nodes(parent), 2)
        promoted = NormalizedAstNode("Root", (NormalizedAstNode("X", (leaf_a, leaf_b)),))
        direct = NormalizedAstNode("Root", (leaf_a, leaf_b))
        self.assertEqual(tree_edit_distance(promoted, direct), 1)

    def test_child_order_is_significant(self):
        left = NormalizedAstNode("Root", (NormalizedAstNode("A"), NormalizedAstNode("B")))
        right = NormalizedAstNode("Root", (NormalizedAstNode("B"), NormalizedAstNode("A")))
        self.assertLess(tree_edit_similarity(left, right), 1.0)

    def test_reused_node_objects_are_counted_as_distinct_tree_occurrences(self):
        shared = NormalizedAstNode("Leaf")
        reused = NormalizedAstNode("Root", (shared, shared))
        copied = NormalizedAstNode("Root", (NormalizedAstNode("Leaf"), NormalizedAstNode("Leaf")))
        changed = NormalizedAstNode("Root", (NormalizedAstNode("Leaf"), NormalizedAstNode("Other")))
        self.assertEqual(tree_edit_distance(reused, copied), 0)
        self.assertEqual(tree_edit_distance(reused, changed), 1)

    def test_maximum_size_is_not_a_valid_general_denominator(self):
        star = NormalizedAstNode("R", tuple(NormalizedAstNode("X") for _ in range(3)))
        chain = NormalizedAstNode("R")
        for _ in range(3):
            chain = NormalizedAstNode("X", (chain,))
        self.assertEqual(count_ast_nodes(star), count_ast_nodes(chain))
        self.assertEqual(tree_edit_distance(star, chain), 5)
        self.assertAlmostEqual(tree_edit_similarity(star, chain), 1 - 5 / 7)


class PythonAstTreeEditTest(unittest.TestCase):
    def similarity(self, left: str, right: str) -> float:
        matrix = ast_tree_edit_similarity({"left.py": left, "right.py": right})
        return float(matrix.loc["left.py", "right.py"])

    def test_identity_identifiers_and_literals_are_normalized(self):
        self.assertEqual(self.similarity("def soma(a, b):\n return a + b", "def calcular(x, y):\n return x + y"), 1.0)
        self.assertEqual(self.similarity("def f():\n return 10", "def f():\n return 999"), 1.0)
        self.assertEqual(self.similarity("def f():\n return 'a'", "def f():\n return 'different'"), 1.0)

    def test_all_identifier_positions_are_normalized(self):
        left = """
import package as imported
class Example:
 def calculate(self, parameter):
  local = self.attribute
  return target(named=local)
"""
        right = """
import another as alias
class Renamed:
 def process(instance, argument):
  result = instance.property
  return destination(option=result)
"""
        self.assertEqual(self.similarity(left, right), 1.0)

    def test_literal_categories_and_operators_are_preserved(self):
        self.assertLess(self.similarity("def f():\n return 10", "def f():\n return '10'"), 1.0)
        self.assertLess(self.similarity("def f(a, b):\n return a + b", "def f(a, b):\n return a - b"), 1.0)

        equivalent_literals = [
            ("x = True", "x = False"),
            ("x = 0", "x = 1"),
            ("x = ''", "x = 'text'"),
            ("x = b''", "x = b'bytes'"),
            ("x = 1j", "x = 99j"),
        ]
        for left, right in equivalent_literals:
            with self.subTest(left=left, right=right):
                self.assertEqual(self.similarity(left, right), 1.0)
        self.assertLess(self.similarity("x = None", "x = False"), 1.0)
        self.assertLess(self.similarity("x = b'value'", "x = 'value'"), 1.0)

    def test_python_operators_are_all_structural(self):
        pairs = [
            ("x = a + b", "x = a - b"),
            ("x = a * b", "x = a / b"),
            ("x = a % b", "x = a * b"),
            ("x = a == b", "x = a != b"),
            ("x = a < b", "x = a > b"),
            ("x = a <= b", "x = a >= b"),
            ("x = a and b", "x = a or b"),
            ("x = not a", "x = -a"),
            ("a += b", "a -= b"),
            ("x = a & b", "x = a | b"),
            ("x = a ** b", "x = a * b"),
            ("x = a < b < c", "x = a > b > c"),
        ]
        for left, right in pairs:
            with self.subTest(left=left, right=right):
                self.assertLess(self.similarity(left, right), 1.0)

    def test_structural_difference_symmetry_identity_and_bounds(self):
        simple = "def f(a):\n return a"
        branching = "def f(a):\n if a:\n  return a\n return 0"
        forward = self.similarity(simple, branching)
        backward = self.similarity(branching, simple)
        self.assertEqual(forward, backward)
        self.assertGreaterEqual(forward, 0.0)
        self.assertLess(forward, 1.0)
        self.assertEqual(self.similarity(simple, simple), 1.0)

        samples = {
            "empty.py": "",
            "assign.py": "x = 1",
            "call.py": "print(x)",
            "loop.py": "for x in values:\n print(x)",
            "class.py": "class Item:\n pass",
        }
        matrix = ast_tree_edit_similarity(samples)
        self.assertTrue(((matrix >= 0) & (matrix <= 1)).all().all())
        self.assertTrue(matrix.equals(matrix.T))
        self.assertTrue((matrix.values.diagonal() == 1).all())

    def test_same_node_frequencies_different_hierarchy(self):
        left = "result = a + (b * c)"
        right = "result = (a + b) * c"
        self.assertEqual(
            sorted(type(node).__name__ for node in ast.walk(ast.parse(left))),
            sorted(type(node).__name__ for node in ast.walk(ast.parse(right))),
        )
        self.assertLess(self.similarity(left, right), 1.0)

    def test_comments_formatting_and_location_metadata_are_ignored(self):
        compact = "def f(x):\n return x + 1"
        formatted = "# comment\n\ndef renamed(value):\n    # another comment\n    return value + 999\n"
        self.assertEqual(self.similarity(compact, formatted), 1.0)
        first = ast.parse(compact)
        second = ast.parse(compact)
        ast.increment_lineno(second, 100)
        self.assertEqual(normalize_ast(first), normalize_ast(second))
        with_type_ignore = ast.parse("x = 1  # type: ignore", type_comments=True)
        without_type_ignore = ast.parse("x = 1")
        self.assertEqual(normalize_ast(with_type_ignore), normalize_ast(without_type_ignore))

    def test_multiple_fragments_have_ordered_synthetic_boundaries(self):
        def similarity(left, right):
            matrix = ast_tree_edit_similarity({"left.md": left, "right.md": right})
            return float(matrix.iloc[0, 1])

        source = """Answer:\n```python\nx = 1\n```\nthen:\n```py\ny = x + 2\n```"""
        tree = normalize_python_source(source)
        self.assertEqual(tree.label, "SyntheticProgram")
        self.assertEqual([child.label for child in tree.children], ["Fragment", "Fragment"])
        reversed_source = """```py\ny = x + 2\n```\n```python\nx = 1\n```"""
        self.assertLess(tree_edit_similarity(tree, normalize_python_source(reversed_source)), 1.0)

        a = "```py\nx = 1\n```"
        b = "```py\nprint(x)\n```"
        c = "```py\nif x:\n y = 2\n```"
        sequences = {
            "ab": f"{a}\n{b}",
            "ba": f"{b}\n{a}",
            "a": a,
            "abc": f"{a}\n{b}\n{c}",
            "ac": f"{a}\n{c}",
        }
        self.assertEqual(similarity(sequences["ab"], sequences["ab"]), 1.0)
        self.assertLess(similarity(sequences["ab"], sequences["ba"]), 1.0)
        self.assertLess(similarity(sequences["ab"], sequences["a"]), 1.0)
        self.assertLess(similarity(sequences["a"], sequences["ab"]), 1.0)
        self.assertLess(similarity(sequences["abc"], sequences["ac"]), 1.0)
        empty_fences = normalize_python_source("```py\n\n```\n```python\n\n```")
        self.assertEqual(empty_fences.children, ())

    def test_modern_python_syntax_is_supported(self):
        samples = [
            "async def f(x):\n return await x",
            "@decorator\ndef f():\n yield from values",
            "x = [value async for value in values if value]",
            "def f():\n yield 1",
            "if (value := call()):\n pass",
            "match value:\n case {'key': captured}:\n  return captured",
            "value: list[int] = []",
            "f = lambda x: x + 1",
            "def outer():\n def inner():\n  return 1\n return inner",
        ]
        for source in samples:
            with self.subTest(source=source):
                self.assertEqual(self.similarity(source, source), 1.0)

    def test_topology_property_cases(self):
        leaf = NormalizedAstNode("Leaf")
        chain = leaf
        for _ in range(200):
            chain = NormalizedAstNode("Node", (chain,))
        wide = NormalizedAstNode("Root", tuple(NormalizedAstNode("Leaf") for _ in range(200)))

        def balanced(depth: int) -> NormalizedAstNode:
            return leaf if depth == 0 else NormalizedAstNode("Node", (balanced(depth - 1), balanced(depth - 1)))

        generator = random.Random(31)

        def random_tree(depth: int = 0) -> NormalizedAstNode:
            count = 0 if depth == 4 else generator.randrange(3)
            return NormalizedAstNode(str(generator.randrange(4)), tuple(random_tree(depth + 1) for _ in range(count)))

        trees = [leaf, chain, wide, balanced(7), *(random_tree() for _ in range(12))]
        for left in trees:
            self.assertEqual(tree_edit_similarity(left, left), 1.0)
            for right in trees:
                value = tree_edit_similarity(left, right)
                self.assertGreaterEqual(value, 0.0)
                self.assertLessEqual(value, 1.0)
                self.assertEqual(value, tree_edit_similarity(right, left))

    def test_public_registry_and_parse_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "left.py").write_text("value = a + b", encoding="utf-8")
            (root / "right.py").write_text("other = x + y", encoding="utf-8")
            result = analyze(root, metrics=AST_TREE_EDIT_METRIC)
            self.assertEqual(result.matrices[AST_TREE_EDIT_METRIC].iloc[0, 1], 1.0)
            self.assertEqual(result.metadata["metric_normalizations"][AST_TREE_EDIT_METRIC], "python_normalized_ast_tree_v3")
            (root / "invalid.py").write_text("def invalid(:", encoding="utf-8")
            with self.assertRaises(AnalysisError):
                analyze(root, metrics=AST_TREE_EDIT_METRIC)


if __name__ == "__main__":
    unittest.main()
