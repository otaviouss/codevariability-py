import tempfile
import unittest
from pathlib import Path

from codevariability import AnalysisError, analyze
from codevariability.normalization import code_tokens


class MethodologyAlignmentTest(unittest.TestCase):
    def test_documented_java_loop_is_tokenized_as_lexemes(self):
        self.assertEqual(
            code_tokens("for (int i = 0; i < n; i++)", "sample.java"),
            ["for", "(", "int", "i", "=", "0", ";", "i", "<", "n", ";", "i", "++", ")"],
        )

    def test_textual_metrics_use_complete_response_while_sequence_metrics_use_code(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.md"
            second = root / "second.md"
            first.write_text("Short explanation.\n```python\nreturn value + 1\n```", encoding="utf-8")
            second.write_text("A completely different natural-language answer.\n```python\nreturn value + 1\n```", encoding="utf-8")

            result = analyze([first, second], metrics=["cosine", "jaccard", "lcs", "levenshtein"])

            self.assertLess(result.matrices["cosine"].iloc[0, 1], 1)
            self.assertLess(result.matrices["jaccard"].iloc[0, 1], 1)
            self.assertEqual(result.matrices["lcs"].iloc[0, 1], 1)
            self.assertEqual(result.matrices["levenshtein"].iloc[0, 1], 1)

    def test_code_sequence_metrics_compare_lexemes_not_characters(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            left = root / "left.unknown"
            right = root / "right.unknown"
            left.write_text("ab c", encoding="utf-8")
            right.write_text("a bc", encoding="utf-8")

            result = analyze([left, right], metrics=["lcs", "levenshtein"])

            self.assertEqual(code_tokens("ab c", "left.unknown"), ["ab", "c"])
            self.assertEqual(code_tokens("a bc", "right.unknown"), ["a", "bc"])
            self.assertEqual(result.matrices["lcs"].iloc[0, 1], 0)
            self.assertEqual(result.matrices["levenshtein"].iloc[0, 1], 0)

    def test_lexer_removes_comments_and_formatting_but_preserves_operators(self):
        compact = "function f(a, b) { return a + b; }"
        formatted = "// explanation\nfunction f(a,b) {\n  return a + b;\n}"
        changed = "function f(a, b) { return a - b; }"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = []
            for name, source in (("compact.js", compact), ("formatted.js", formatted), ("changed.js", changed)):
                path = root / name
                path.write_text(source, encoding="utf-8")
                paths.append(path)
            result = analyze(paths, metrics=["lcs", "levenshtein"])

            for metric in result.metrics:
                self.assertEqual(result.matrices[metric].loc["compact.js", "formatted.js"], 1)
                self.assertLess(result.matrices[metric].loc["compact.js", "changed.js"], 1)

    def test_classical_token_levenshtein_uses_unit_substitution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            left = root / "left.java"
            right = root / "right.java"
            left.write_text("return a + b;", encoding="utf-8")
            right.write_text("return a - b;", encoding="utf-8")
            result = analyze([left, right], metrics="levenshtein")

            # Five tokens and one unit-cost substitution: 1 - 1/5 = 0.8.
            self.assertEqual(result.matrices["levenshtein"].iloc[0, 1], 0.8)

    def test_arbitrary_utf8_extensions_are_supported_for_non_ast_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Main.java").write_text("class Main {}", encoding="utf-8")
            (root / "Other.java").write_text("class Other {}", encoding="utf-8")

            result = analyze(root, metrics="all")
            filtered = analyze(root, metrics="all", extensions=["java"])

            self.assertEqual(result.metrics, ("cosine", "jaccard", "lcs", "levenshtein"))
            self.assertEqual(filtered.metrics, result.metrics)
            with self.assertRaisesRegex(AnalysisError, "incompatível"):
                analyze(root, metrics="ast_tree_edit_similarity")

    def test_reproducibility_metadata_identifies_algorithms_and_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.go").write_text("package main\n", encoding="utf-8")
            result = analyze(root, metrics="all")

            self.assertEqual(result.metadata["metric_ids"]["cosine"], "cosine_bag_of_words_text_v1")
            self.assertEqual(result.metadata["metric_ids"]["lcs"], "lcs_code_tokens_v1")
            self.assertEqual(result.metadata["metric_dimensions"]["cosine"], "textual")
            self.assertEqual(result.metadata["metric_dimensions"]["lcs"], "syntactic_token_sequence")
            self.assertIn("pygments", result.metadata["runtime_versions"])
            self.assertIn("rapidfuzz", result.metadata["runtime_versions"])


if __name__ == "__main__":
    unittest.main()
