import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from codevariability import AnalysisError, analyze, load_matrix_json
from codevariability.normalization import code_tokens, text_tokens


class AnalysisTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        (root / "sample1.jsx").write_text("const Button = () => <button>Save</button>;", encoding="utf-8")
        (root / "sample2.jsx").write_text("const Button = () => <button>Send</button>;", encoding="utf-8")
        (root / "sample3.jsx").write_text("const Card = () => <section>Different</section>;", encoding="utf-8")

    def tearDown(self):
        self.directory.cleanup()

    def test_selected_metrics_are_normalized_and_summarized(self):
        result = analyze(self.directory.name, metrics=["cosine", "jaccard", "lcs", "levenshtein"])
        self.assertEqual(set(result.matrices), {"cosine", "jaccard", "lcs", "levenshtein"})
        self.assertEqual(result.statistics.loc["cosine", "n_pairs"], 3)
        for matrix in result.matrices.values():
            self.assertTrue(((matrix >= 0) & (matrix <= 1)).all().all())
            self.assertTrue((matrix.values.diagonal() == 1).all())

    def test_all_means_every_metric_applicable_to_all_input_files(self):
        javascript = analyze(self.directory.name, metrics="all")
        self.assertNotIn("ast_tree_edit_similarity", javascript.matrices)

        python_directory = Path(self.directory.name) / "python"
        python_directory.mkdir()
        (python_directory / "a.py").write_text("value = left + right", encoding="utf-8")
        (python_directory / "b.py").write_text("result = x + y", encoding="utf-8")
        python = analyze(python_directory, metrics="all")
        self.assertIn("ast_tree_edit_similarity", python.matrices)
        self.assertEqual(
            python.metadata["metric_ids"]["ast_tree_edit_similarity"],
            "ast_tree_edit_similarity_v2",
        )

    def test_parser_specific_metric_rejects_incompatible_extensions(self):
        with self.assertRaisesRegex(AnalysisError, "incompatível"):
            analyze(self.directory.name, metrics="ast_tree_edit_similarity")

    def test_cosine_accepts_single_character_identifiers(self):
        root = Path(self.directory.name)
        (root / "sample1.jsx").write_text("x", encoding="utf-8")
        (root / "sample2.jsx").write_text("y", encoding="utf-8")
        (root / "sample3.jsx").write_text("x", encoding="utf-8")
        matrix = analyze(root, metrics="cosine").matrices["cosine"]
        self.assertEqual(matrix.loc["sample1.jsx", "sample2.jsx"], 0.0)
        self.assertEqual(matrix.loc["sample1.jsx", "sample3.jsx"], 1.0)

    def test_text_and_code_preprocessing_have_distinct_inputs(self):
        source = 'const url = "https://example.com"; const text = `hello // world`;'
        self.assertIn("hello", text_tokens(source))
        self.assertIn('"https://example.com"', code_tokens(source, "source.js"))

    def test_medoid_and_equal_composite(self):
        result = analyze(self.directory.name, metrics=["cosine", "jaccard"])
        medoid = result.medoid("cosine")
        self.assertIn(medoid["file"], result.files)
        composite = result.composite()
        self.assertEqual(composite.attrs["kind"], "exploratory_composite_similarity")

    def test_external_matrix_can_be_loaded_and_attached(self):
        result = analyze(self.directory.name, metrics=["cosine"])
        external = pd.DataFrame(
            [[1.0, 0.8, 0.2], [0.8, 1.0, 0.3], [0.2, 0.3, 1.0]],
            index=result.files,
            columns=result.files,
        )
        external.attrs.update({
            "metric_id": "ast_node_type_multiset_jaccard_v1",
            "normalization": "babel_ast_node_type_multiset_v1",
            "dimension": "structural_ast",
        })
        augmented = result.with_metric("ast_node_type_multiset_jaccard", external)
        self.assertEqual(augmented.medoid("ast_node_type_multiset_jaccard")["file"], "sample2.jsx")
        self.assertEqual(len(augmented.composite().columns), 3)
        self.assertEqual(
            augmented.metadata["metric_ids"]["ast_node_type_multiset_jaccard"],
            "ast_node_type_multiset_jaccard_v1",
        )

    def test_external_matrix_requires_the_same_files(self):
        result = analyze(self.directory.name, metrics=["cosine"])
        invalid = pd.DataFrame([[1.0]], index=["other.jsx"], columns=["other.jsx"])
        with self.assertRaises(AnalysisError):
            result.with_metric("external", invalid)

    def test_external_matrix_rejects_non_numeric_values(self):
        result = analyze(self.directory.name, metrics=["cosine"])
        invalid = pd.DataFrame("invalid", index=result.files, columns=result.files)
        with self.assertRaises(AnalysisError):
            result.with_metric("external", invalid)

    def test_adapter_json_schema_is_loaded(self):
        payload = '{"schema_version":"codevariability.matrix.v1","metric":"ast","metric_id":"ast_v1","files":["a.jsx"],"matrix":[[1.0]],"metadata":{"normalization":"tree_v1","metric_dimensions":{"ast":"structural_ast"}}}'
        path = Path(self.directory.name) / "matrix.json"
        path.write_text(payload, encoding="utf-8")
        metric, matrix = load_matrix_json(path)
        self.assertEqual(metric, "ast")
        self.assertEqual(float(matrix.iloc[0, 0]), 1.0)
        self.assertEqual(matrix.attrs["metric_id"], "ast_v1")
        self.assertEqual(matrix.attrs["normalization"], "tree_v1")

    def test_cli_exports_requested_analysis(self):
        output = Path(self.directory.name) / "output"
        subprocess.run(
            [sys.executable, "-m", "codevariability.cli", "analyze", self.directory.name, "--metrics", "cosine", "--output", str(output)],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertTrue((output / "analysis.json").exists())

    def test_cli_exports_complete_excel_workbook(self):
        output = Path(self.directory.name) / "analysis.xlsx"
        subprocess.run(
            [sys.executable, "-m", "codevariability.cli", "analyze", self.directory.name,
             "--metrics", "cosine", "--output", str(output)],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            set(pd.ExcelFile(output).sheet_names),
            {"Summary", "Metadata", "Representativeness", "Ranking", "Statistics", "cosine"},
        )

    def test_export_accepts_one_format_as_a_string(self):
        output = Path(self.directory.name) / "output"
        analyze(self.directory.name, metrics="cosine").export(output, formats="json")
        self.assertTrue((output / "analysis.json").exists())
        self.assertFalse((output / "statistics.csv").exists())

    def test_invalid_inputs_raise_public_analysis_error(self):
        with self.assertRaises(AnalysisError):
            analyze(Path(self.directory.name) / "missing.jsx")
        with self.assertRaises(AnalysisError):
            analyze([Path(self.directory.name)])
        with self.assertRaises(AnalysisError):
            analyze(self.directory.name, metrics=[])
        markdown = Path(self.directory.name) / "answer.md"
        markdown.write_text("Explanation.\n```python\nvalue = 1\n```", encoding="utf-8")
        markdown_result = analyze([markdown], metrics="all")
        self.assertIn("ast_tree_edit_similarity", markdown_result.metrics)

    def test_composite_rejects_non_finite_weights(self):
        result = analyze(self.directory.name, metrics=["cosine"])
        for weight in (math.nan, math.inf, "invalid"):
            with self.subTest(weight=weight), self.assertRaises(AnalysisError):
                result.composite(weights={"cosine": weight})

    def test_adapter_json_rejects_malformed_content(self):
        path = Path(self.directory.name) / "matrix.json"
        path.write_text("{invalid", encoding="utf-8")
        with self.assertRaises(AnalysisError):
            load_matrix_json(path)
        path.write_text(json.dumps({"schema_version": "codevariability.matrix.v1", "metric": "ast_v1", "files": ["a.jsx"], "matrix": []}), encoding="utf-8")
        with self.assertRaises(AnalysisError):
            load_matrix_json(path)
