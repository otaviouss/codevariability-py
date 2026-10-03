import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from codevariability import AnalysisError, compare_groups
from codevariability.metrics import CALCULATORS


class GroupComparisonTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.group_a = root / "alpha"
        self.group_b = root / "beta"
        self.group_a.mkdir()
        self.group_b.mkdir()
        for name, source in {
            "one.py": "def total(a, b):\n    return a + b\n",
            "two.py": "def total(x, y):\n    return x + y\n",
            "three.py": "def total(a, b):\n    return a + b + 1\n",
        }.items():
            (self.group_a / name).write_text(source, encoding="utf-8")
        for name, source in {
            "one.py": "class Item:\n    pass\n",
            "two.py": "while active:\n    process()\n",
            "three.py": "for value in values:\n    print(value)\n",
        }.items():
            (self.group_b / name).write_text(source, encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def test_compares_internal_homogeneity_and_between_group_similarity(self):
        result = compare_groups(
            self.group_a,
            self.group_b,
            metrics=["cosine", "jaccard"],
            group_names=("alpha", "beta"),
            permutations=199,
            random_state=7,
        )

        self.assertEqual(result.group_names, ("alpha", "beta"))
        self.assertEqual(result.metrics, ("cosine", "jaccard"))
        self.assertEqual(len(result.files["alpha"]), 3)
        self.assertIn("dimension:textual", result.summary.index)
        self.assertIn("overall", result.summary.index)
        self.assertEqual(result.overview.index.tolist(), ["Textual", "Geral"])
        self.assertIn("evidência", result.interpretation)
        for column in (
            "within_alpha",
            "within_beta",
            "between_groups",
            "within_difference",
            "separation",
            "p_value_homogeneity",
            "p_value_separation",
            "p_value_homogeneity_holm",
            "p_value_separation_holm",
        ):
            self.assertIn(column, result.summary)
        self.assertTrue(
            result.summary.filter(like="p_value").map(lambda value: 0 <= value <= 1).all().all()
        )

        metric_rows = result.summary.loc[["cosine", "jaccard"]]
        dimension = result.summary.loc["dimension:textual"]
        for column in ("within_alpha", "within_beta", "between_groups", "within_difference", "separation"):
            self.assertAlmostEqual(dimension[column], metric_rows[column].mean())
            self.assertAlmostEqual(result.summary.loc["overall", column], dimension[column])

    def test_is_deterministic_and_calculates_each_matrix_once(self):
        original = CALCULATORS["cosine"]
        calls = 0

        def counted(sources):
            nonlocal calls
            calls += 1
            return original(sources)

        with patch.dict(CALCULATORS, {"cosine": counted}):
            first = compare_groups(
                self.group_a, self.group_b, metrics="cosine", permutations=49, random_state=11
            )
        second = compare_groups(
            self.group_a, self.group_b, metrics="cosine", permutations=49, random_state=11
        )

        self.assertEqual(calls, 1)
        pd.testing.assert_frame_equal(first.summary, second.summary)

    def test_exports_csv_json_and_excel(self):
        result = compare_groups(
            self.group_a, self.group_b, metrics="cosine", permutations=19
        )
        output = Path(self.temporary.name) / "output"
        result.export(output)
        workbook = output / "comparison.xlsx"
        result.to_excel(workbook)

        self.assertTrue((output / "group_comparison.csv").is_file())
        self.assertTrue((output / "group_comparison.json").is_file())
        self.assertEqual(
            set(pd.ExcelFile(workbook).sheet_names),
            {"Overview", "Technical details", "Metrics", "Files"},
        )

    def test_includes_javascript_ted_through_the_node_adapter(self):
        from codevariability.group_comparison import _javascript_adapter_command
        try:
            command = _javascript_adapter_command()
        except AnalysisError:
            self.skipTest("Adaptador JavaScript opcional indisponível; instale-o para testar integração real.")
        probe = subprocess.run([*command, "--version"], capture_output=True, text=True, timeout=20)
        if probe.returncode and "Cannot find module '@babel/parser'" in probe.stderr:
            self.skipTest("Dependência opcional @babel/parser indisponível; instale o adaptador.")
        probe.check_returncode()
        root = Path(self.temporary.name)
        javascript_a = root / "javascript-a"
        javascript_b = root / "javascript-b"
        javascript_a.mkdir()
        javascript_b.mkdir()
        (javascript_a / "same.js").write_text("const total = a + b;", encoding="utf-8")
        (javascript_a / "other.js").write_text("const value = x + y;", encoding="utf-8")
        (javascript_b / "same.js").write_text("if (ready) run();", encoding="utf-8")
        (javascript_b / "other.js").write_text("while (ready) run();", encoding="utf-8")

        progress = []
        cache = root / "cache"
        result = compare_groups(
            javascript_a,
            javascript_b,
            metrics="all",
            include_ast=True,
            permutations=19,
            random_state=3,
            progress=progress.append,
            cache_dir=cache,
        )

        self.assertIn("ast_tree_edit_similarity", result.metrics)
        self.assertIn("dimension:structural_ast", result.summary.index)
        self.assertIn("Estrutural (AST TED)", result.overview.index)
        self.assertTrue(result.metadata["javascript_ast_included"])
        adapter_metadata = result.metadata["external_adapters"]["ast_tree_edit_similarity"]
        self.assertTrue(adapter_metadata["runtime_versions"]["node"])
        self.assertTrue(adapter_metadata["runtime_versions"]["babel_parser"])
        self.assertEqual(adapter_metadata["input_sha256"], result.metadata["input_sha256"])
        self.assertTrue(any("3/6 pairs" in message for message in progress))
        self.assertEqual(progress[-1], "Comparação concluída.")
        self.assertEqual(result.metadata["javascript_ast_cache"]["misses"], 6)

        repeated = compare_groups(
            javascript_a,
            javascript_b,
            metrics="all",
            include_ast=True,
            permutations=19,
            random_state=3,
            cache_dir=cache,
        )
        self.assertEqual(repeated.metadata["javascript_ast_cache"]["hits"], 6)
        pd.testing.assert_frame_equal(result.summary, repeated.summary)

    def test_requires_two_files_per_independent_group(self):
        single = Path(self.temporary.name) / "single"
        single.mkdir()
        (single / "only.py").write_text("value = 1\n", encoding="utf-8")
        with self.assertRaisesRegex(AnalysisError, "pelo menos dois"):
            compare_groups(single, self.group_b, permutations=9)

    def test_three_groups_have_global_and_jointly_corrected_pairwise_tests(self):
        third = Path(self.temporary.name) / "gamma"
        third.mkdir()
        (third / "one.py").write_text("items = [x for x in values]\n", encoding="utf-8")
        (third / "two.py").write_text("items = list(values)\n", encoding="utf-8")

        original = CALCULATORS["cosine"]
        calls = 0

        def counted(sources):
            nonlocal calls
            calls += 1
            return original(sources)

        with patch.dict(CALCULATORS, {"cosine": counted}):
            result = compare_groups(
                {"alpha": self.group_a, "beta": self.group_b, "gamma": third},
                metrics="cosine",
                permutations=49,
                random_state=5,
            )
            _ = result.overview
            _ = result.pairwise_overview
            _ = result.interpretation
            result.export(Path(self.temporary.name) / "multigroup-output")
            result.to_excel(Path(self.temporary.name) / "multigroup.xlsx")

        self.assertEqual(calls, 1)
        pd.testing.assert_frame_equal(result.overview, result.overview)
        pd.testing.assert_frame_equal(result.pairwise_overview, result.pairwise_overview)

        self.assertEqual(result.group_names, ("alpha", "beta", "gamma"))
        self.assertIn("overall", result.global_test.index)
        self.assertEqual(
            set(result.pairwise.index.droplevel("measure").unique()),
            {("alpha", "beta"), ("alpha", "gamma"), ("beta", "gamma")},
        )
        self.assertEqual(result.within_groups.shape[0], 3)
        self.assertEqual(result.between_groups.shape[0], 3)
        self.assertEqual(result.pairwise_overview.shape[0], 3)
        self.assertTrue(
            result.global_test.filter(like="p_value").map(lambda value: 0 <= value <= 1).all().all()
        )
        self.assertTrue(
            result.pairwise.filter(like="p_value").map(lambda value: 0 <= value <= 1).all().all()
        )

    def test_two_group_mapping_reduces_to_the_binary_statistics(self):
        binary = compare_groups(
            self.group_a,
            self.group_b,
            metrics="cosine",
            group_names=("alpha", "beta"),
            permutations=49,
            random_state=13,
        )
        multiple = compare_groups(
            {"alpha": self.group_a, "beta": self.group_b},
            metrics="cosine",
            permutations=49,
            random_state=13,
        )
        pair = multiple.pairwise.loc[("alpha", "beta", "cosine")]
        row = binary.summary.loc["cosine"]
        for column in ("between_groups", "within_difference", "separation"):
            self.assertAlmostEqual(pair[column], row[column])
        self.assertAlmostEqual(pair["p_value_homogeneity"], row["p_value_homogeneity"])
        self.assertAlmostEqual(pair["p_value_separation"], row["p_value_separation"])


if __name__ == "__main__":
    unittest.main()
