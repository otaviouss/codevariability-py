import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from codevariability import AnalysisResult, analyze
from codevariability.metrics import CALCULATORS

SOURCES = {
    "solution_01.py": "def combine(a, b):\n    return a + b\n",
    "solution_02.py": "def calculate(x, y):\n    return x + y\n",
    "solution_03.py": "def combine(a, b):\n    return a - b\n",
    "solution_04.py": "def combine(a, b):\n    if a:\n        return a + b\n    return b\n",
    "solution_05.py": "def combine(a, b):\n    while a:\n        a -= 1\n    return a + b\n",
}


class UserFlowTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        for name, source in SOURCES.items():
            (self.root / name).write_text(source, encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def test_one_metric_exposes_complete_analysis(self):
        result = analyze(self.root, metrics="ast_tree_edit_similarity")

        self.assertEqual(result.metrics, ("ast_tree_edit_similarity",))
        self.assertEqual(result.matrices["ast_tree_edit_similarity"].shape, (5, 5))
        self.assertEqual(result.statistics.loc["ast_tree_edit_similarity", "n_pairs"], 10)
        self.assertEqual(
            list(result.representativeness.columns),
            ["file", "ast_tree_edit_similarity", "structural_score", "overall_score", "overall_dispersion"],
        )
        self.assertEqual(result.ranking.iloc[0]["file"], result.most_representative["file"])
        self.assertEqual(result.ranking.iloc[-1]["file"], result.most_distinct["file"])
        self.assertEqual(
            result.rankings_by_metric["ast_tree_edit_similarity"].iloc[0]["file"],
            result.medoid("ast_tree_edit_similarity")["file"],
        )

    def test_all_metrics_feed_every_aggregate(self):
        result = analyze(self.root, metrics="all")

        self.assertEqual(set(result.statistics.index), set(result.metrics))
        self.assertEqual(result.metadata["metrics"], list(result.metrics))
        self.assertEqual(set(result.rankings_by_metric), set(result.metrics))
        self.assertEqual(
            set(result.representativeness.columns),
            {
                "file",
                "textual_score",
                "syntactic_score",
                "structural_score",
                "overall_score",
                "overall_dispersion",
                *result.metrics,
            },
        )
        expected_textual = result.representativeness[["cosine", "jaccard"]].mean(axis=1)
        expected_syntactic = result.representativeness[["lcs", "levenshtein"]].mean(axis=1)
        expected_structural = result.representativeness["ast_tree_edit_similarity"]
        expected = pd.concat(
            [expected_textual, expected_syntactic, expected_structural], axis=1
        ).mean(axis=1)
        pd.testing.assert_series_equal(
            result.representativeness["textual_score"], expected_textual, check_names=False
        )
        pd.testing.assert_series_equal(
            result.representativeness["syntactic_score"], expected_syntactic, check_names=False
        )
        pd.testing.assert_series_equal(
            result.representativeness["structural_score"], expected_structural, check_names=False
        )
        pd.testing.assert_series_equal(
            result.representativeness["overall_score"], expected, check_names=False
        )
        self.assertEqual(
            result.metadata["overall_aggregation"]["dimension_metrics"],
            {
                "textual": ["cosine", "jaccard"],
                "syntactic_token_sequence": ["lcs", "levenshtein"],
                "structural_ast": ["ast_tree_edit_similarity"],
            },
        )
        for metric, ranking in result.rankings_by_metric.items():
            expected_scores = result.representativeness.set_index("file")[metric]
            actual_scores = ranking.set_index("file")["score"]
            pd.testing.assert_series_equal(
                actual_scores.sort_index(), expected_scores.sort_index(), check_names=False
            )
            self.assertTrue(ranking["score"].is_monotonic_decreasing)
        output = self.root / "export"
        result.export(output, formats="json")
        payload = json.loads((output / "analysis.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["metrics"], list(result.metrics))
        self.assertEqual(payload["metadata"]["metrics"], list(result.metrics))

    def test_excel_contains_summary_tables_and_every_matrix(self):
        result = analyze(self.root, metrics=["jaccard", "ast_tree_edit_similarity"])
        workbook = self.root / "analysis.xlsx"
        result.to_excel(workbook)

        excel = pd.ExcelFile(workbook)
        self.assertEqual(
            set(excel.sheet_names),
            {"Summary", "Metadata", "Representativeness", "Ranking", "Statistics", "jaccard", "ast_tree_edit_similarity"},
        )
        self.assertFalse(any("_v1" in sheet for sheet in excel.sheet_names))
        self.assertEqual(pd.read_excel(workbook, sheet_name="Ranking").shape, (5, 3))
        matrix = pd.read_excel(workbook, sheet_name="ast_tree_edit_similarity", index_col=0)
        self.assertEqual(matrix.shape, (5, 5))
        summary = pd.read_excel(workbook, sheet_name="Summary").set_index("field")["value"]
        self.assertEqual(summary["metrics_used"], "jaccard, ast_tree_edit_similarity")
        ranking = pd.read_excel(workbook, sheet_name="Ranking")
        self.assertEqual(ranking.iloc[0]["file"], summary["most_representative_file"])
        self.assertEqual(ranking.iloc[-1]["file"], summary["most_distinct_file"])
        self.assertTrue(ranking["score"].is_monotonic_decreasing)
        self.assertTrue(pd.api.types.is_numeric_dtype(ranking["score"]))
        metadata = pd.read_excel(workbook, sheet_name="Metadata").set_index("field")["value"]
        self.assertIn("jaccard_word_set_text_v1", metadata["metric_ids"])
        self.assertIn("pygments", metadata["runtime_versions"])

    def test_analysis_and_ranking_are_deterministic(self):
        first = analyze(self.root, metrics="all")
        second = analyze(self.root, metrics="all")

        pd.testing.assert_frame_equal(first.ranking, second.ranking)
        pd.testing.assert_frame_equal(first.representativeness, second.representativeness)
        for metric in first.metrics:
            pd.testing.assert_frame_equal(first.matrices[metric], second.matrices[metric])

    def test_legacy_ast_baseline_does_not_count_twice_when_ted_is_present(self):
        result = analyze(self.root, metrics="all")
        before = result.representativeness["overall_score"].copy()
        legacy = pd.DataFrame(0.0, index=result.files, columns=result.files)
        for file in result.files:
            legacy.loc[file, file] = 1.0
        legacy.attrs["dimension"] = "structural_ast"
        legacy.attrs["metric_id"] = "ast_node_type_multiset_jaccard_v1"

        augmented = result.with_metric("ast_node_type_multiset_jaccard", legacy)

        pd.testing.assert_series_equal(
            augmented.representativeness["structural_score"],
            augmented.representativeness["ast_tree_edit_similarity"],
            check_names=False,
        )
        pd.testing.assert_series_equal(
            augmented.representativeness["overall_score"], before, check_names=False
        )
        self.assertNotIn(
            "ast_node_type_multiset_jaccard",
            augmented.metadata["overall_aggregation"]["dimension_metrics"]["structural_ast"],
        )

    def test_accessing_aggregates_does_not_recalculate_ted(self):
        original = CALCULATORS["ast_tree_edit_similarity"]
        calls = 0

        def counted(sources, **kwargs):
            nonlocal calls
            calls += 1
            return original(sources, **kwargs)

        with patch.dict(CALCULATORS, {"ast_tree_edit_similarity": counted}):
            result = analyze(self.root, metrics="ast_tree_edit_similarity")
            _ = result.statistics
            _ = result.representativeness
            _ = result.rankings_by_metric
            _ = result.ranking
            _ = result.most_representative
            _ = result.most_distinct
            result.export(self.root / "cached-export")
            result.to_excel(self.root / "cached-analysis.xlsx")

        self.assertEqual(calls, 1)
        pd.testing.assert_frame_equal(result.statistics, result.statistics)
        pd.testing.assert_frame_equal(result.representativeness, result.representativeness)
        self.assertEqual(tuple(result.rankings_by_metric), tuple(result.rankings_by_metric))
        pd.testing.assert_frame_equal(result.ranking, result.ranking)

    def test_ties_are_broken_by_filename(self):
        for file in self.root.iterdir():
            if file.suffix == ".py":
                file.write_text("value = 1\n", encoding="utf-8")

        result = analyze(self.root, metrics="ast_tree_edit_similarity")

        self.assertEqual(result.ranking["file"].tolist(), sorted(SOURCES))
        self.assertTrue((result.ranking["score"] == 1.0).all())

    def test_formula_excludes_any_diagonal_and_counts_each_peer_once(self):
        files = ["a.py", "b.py", "c.py"]
        matrix = pd.DataFrame(
            [[0.11, 0.2, 0.4], [0.2, 0.22, 0.8], [0.4, 0.8, 0.33]],
            index=files,
            columns=files,
        )
        # Construct a valid result, then exercise the formula on deliberately
        # changed public diagonal values; construction now validates invariants.
        initial = matrix.copy()
        for i in range(len(files)):
            initial.iat[i, i] = 1.0
        result = AnalysisResult({"controlled": initial}, files)
        for i in range(len(files)):
            result.matrices["controlled"].iat[i, i] = matrix.iat[i, i]
        scores = result.representativeness.set_index("file")["controlled"]

        self.assertAlmostEqual(scores["a.py"], (0.2 + 0.4) / 2)
        self.assertAlmostEqual(scores["b.py"], (0.2 + 0.8) / 2)
        self.assertAlmostEqual(scores["c.py"], (0.4 + 0.8) / 2)
        self.assertEqual(result.rankings_by_metric["controlled"]["file"].tolist(), ["c.py", "b.py", "a.py"])
        self.assertEqual(result.medoid("controlled")["file"], "c.py")

    def test_partial_ties_and_custom_composite_do_not_change_standard_ranking(self):
        files = ["b.py", "c.py", "a.py"]
        first = pd.DataFrame(
            [[1, 0.2, 0.8], [0.2, 1, 0.2], [0.8, 0.2, 1]],
            index=files,
            columns=files,
        )
        second = pd.DataFrame(
            [[1, 0.4, 0.6], [0.4, 1, 0.4], [0.6, 0.4, 1]],
            index=files,
            columns=files,
        )
        result = AnalysisResult({"first": first, "second": second}, files)
        before = result.ranking.copy()

        result.composite(weights={"first": 0.9, "second": 0.1})

        pd.testing.assert_frame_equal(result.ranking, before)
        self.assertEqual(result.ranking["file"].tolist(), ["a.py", "b.py", "c.py"])

    def test_statistics_for_one_two_and_three_files(self):
        one = pd.DataFrame([[1.0]], index=["a"], columns=["a"])
        one_stats = AnalysisResult({"m": one}, ["a"]).statistics.loc["m"]
        self.assertEqual(one_stats["n_pairs"], 0)
        for field in ("mean_similarity", "median_similarity", "std_similarity",
                      "min_similarity", "max_similarity", "q1_similarity", "q3_similarity"):
            self.assertIsNone(one_stats[field])

        two = pd.DataFrame([[1.0, 0.25], [0.25, 1.0]], index=["a", "b"], columns=["a", "b"])
        two_stats = AnalysisResult({"m": two}, ["a", "b"]).statistics.loc["m"]
        self.assertEqual(two_stats["n_pairs"], 1)
        self.assertEqual(two_stats["std_similarity"], 0.0)
        self.assertEqual(two_stats["q1_similarity"], 0.25)
        self.assertEqual(two_stats["q3_similarity"], 0.25)

        files = ["a", "b", "c"]
        three = pd.DataFrame([[1, 0.2, 0.4], [0.2, 1, 0.8], [0.4, 0.8, 1]], index=files, columns=files)
        three_stats = AnalysisResult({"m": three}, files).statistics.loc["m"]
        self.assertEqual(three_stats["n_pairs"], 3)
        self.assertAlmostEqual(three_stats["mean_similarity"], (0.2 + 0.4 + 0.8) / 3)
        self.assertAlmostEqual(three_stats["std_similarity"], pd.Series([0.2, 0.4, 0.8]).std(ddof=1))
        self.assertAlmostEqual(three_stats["q1_similarity"], 0.3)
        self.assertAlmostEqual(three_stats["q3_similarity"], 0.6)


if __name__ == "__main__":
    unittest.main()
