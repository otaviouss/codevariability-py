"""Public input validation, resource limits, and safe output behavior."""

import ast
import json
import os
import subprocess
import sys

import pandas as pd
import pytest

from codevariability import (
    AnalysisError,
    AnalysisResult,
    CodeDataset,
    analyze,
    compare_groups,
    load_matrix_json,
)
from codevariability.ast_tree_edit import count_ast_nodes, normalize_python_source
from codevariability.group_comparison import _run_adapter
from codevariability.normalization import code_tokens, fenced_code_segments


def pair(tmp_path, left="x=1", right="x=2", extension=".py", **kwargs):
    paths = [tmp_path / (name + extension) for name in ("a", "b")]
    for path, text in zip(paths, (left, right), strict=True):
        path.write_text(text, encoding="utf-8")
    return analyze(paths, **kwargs)


def groups(tmp_path):
    paths = []
    for name in ["a", "b"]:
        directory = tmp_path / name
        directory.mkdir()
        pair(directory)
        paths.append(directory)
    return paths


def test_python_literal_is_full_source(tmp_path):
    source = 's = """```python\nx=1\n```"""\ny=a-b'
    result = pair(tmp_path, source, "x=1")
    assert result.matrices["ast_tree_edit_similarity"].iat[0, 1] < 1
    assert "s" in code_tokens(source, "a.py")


@pytest.mark.parametrize("fence", ["```", "````", "~~~"])
def test_fences_strings_and_info(tmp_path, fence):
    result = pair(
        tmp_path,
        f'{fence}python title=test\ns="```"\n{fence}',
        f'{fence}py\ns="other"\n{fence}',
        ".md",
    )
    assert result.matrices["ast_tree_edit_similarity"].iat[0, 1] == 1
    assert len(fenced_code_segments(f"{fence}python\nx=1\n{fence}")) == 1


def test_indentation_and_coordinates(tmp_path):
    with pytest.raises(AnalysisError, match="linha de origem 1"):
        pair(tmp_path, "  x=1", "x=1")
    with pytest.raises(AnalysisError, match="linha de origem 5"):
        pair(
            tmp_path,
            "explanation\n```python\n\n\n  x=1\n```",
            "```python\nx=1\n```",
            ".md",
        )


@pytest.mark.parametrize(
    "metric",
    [
        "file",
        "textual_score",
        "overall_score",
        "overall_dispersion",
        "rank",
        "",
        None,
        42,
    ],
)
def test_reject_reserved_metrics(tmp_path, metric):
    result = pair(tmp_path, metrics="jaccard")
    with pytest.raises(AnalysisError):
        result.with_metric(metric, result.matrices["jaccard"])
    assert result.files == ["a.py", "b.py"]


@pytest.mark.parametrize("dimension", ["overall", "overall_dispersion", [], None, 42])
def test_external_dimension_validation(tmp_path, dimension):
    result = pair(tmp_path, metrics="jaccard")
    matrix = result.matrices["jaccard"].copy()
    matrix.attrs["dimension"] = dimension
    if dimension == "overall_dispersion":
        # This dimension creates overall_dispersion_score, which is disjoint.
        assert (
            "overall_dispersion_score"
            in result.with_metric("external", matrix).representativeness
        )
    elif dimension is None:
        assert (
            "external_score"
            in result.with_metric("external", matrix).representativeness
        )
    else:
        with pytest.raises(AnalysisError):
            result.with_metric("external", matrix)


def test_generated_column_collision(tmp_path):
    result = pair(tmp_path, metrics="jaccard")
    first = result.matrices["jaccard"].copy()
    first.attrs["dimension"] = "custom"
    augmented = result.with_metric("external", first)
    with pytest.raises(AnalysisError):
        augmented.with_metric("custom_score", first)


def test_recompute_and_copy(tmp_path):
    result = pair(tmp_path, "x", "y", metrics="jaccard")
    assert result.statistics.loc["jaccard", "mean_similarity"] == 0
    original = result.ranking
    result.matrices["jaccard"].iat[0, 1] = 0.9
    result.matrices["jaccard"].iat[1, 0] = 0.9
    assert result.statistics.loc["jaccard", "mean_similarity"] == pytest.approx(0.9)
    assert result.ranking.iloc[0]["score"] == pytest.approx(0.9)
    assert original.iloc[0]["score"] == 0
    augmented = result.with_metric("external", result.matrices["jaccard"])
    augmented.matrices["jaccard"].iat[0, 1] = 0.3
    assert result.matrices["jaccard"].iat[0, 1] == 0.9
    augmented.metadata["runtime_versions"]["python"] = "changed"
    assert result.metadata["runtime_versions"]["python"] != "changed"


def test_extensions_and_names(tmp_path):
    for name in ["²", "a.py", "A.py", "sample10.PY", "sample2.py"]:
        (tmp_path / name).write_text("pass")
    assert "²" in CodeDataset.from_directory(tmp_path).files
    assert list(CodeDataset.from_directory(tmp_path, extensions="py").files) == [
        "A.py",
        "a.py",
        "sample2.py",
        "sample10.PY",
    ]


@pytest.mark.parametrize("value", [None, 42, [None], [{}], [1, "bad"]])
def test_metric_errors_are_domain_errors(tmp_path, value):
    with pytest.raises(AnalysisError):
        pair(tmp_path, metrics=value)


@pytest.mark.parametrize("value", [42, [None], [""], [{}]])
def test_extension_errors_are_domain_errors(tmp_path, value):
    (tmp_path / "a.py").write_text("pass")
    with pytest.raises(AnalysisError):
        CodeDataset.from_directory(tmp_path, value)


@pytest.mark.parametrize(
    "content",
    [
        '{"schema_version":"x","schema_version":"y"}',
        "[" * 20000 + "0" + "]" * 20000,
        json.dumps(
            {
                "schema_version": "codevariability.matrix.v1",
                "metric": "external",
                "files": ["a", "b"],
                "matrix": [[1, 10**400], [10**400, 1]],
            }
        ),
    ],
)
def test_json_invalid_data(tmp_path, content):
    path = tmp_path / "matrix.json"
    path.write_text(content)
    with pytest.raises(AnalysisError):
        load_matrix_json(path)


@pytest.mark.parametrize("value", [True, "0.5", float("inf"), float("nan"), -1, 1.1])
def test_strict_json_numbers(tmp_path, value):
    path = tmp_path / "matrix.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "codevariability.matrix.v1",
                "metric": "external",
                "files": ["a", "b"],
                "matrix": [[1, value], [value, 1]],
            }
        )
    )
    with pytest.raises(AnalysisError):
        load_matrix_json(path)


def test_deep_ast_without_recursion(tmp_path):
    source = "x=" + "1+" * 1200 + "1"
    ast.parse(source)
    result = pair(tmp_path, source, source, metrics="ast_tree_edit_similarity")
    assert result.matrices["ast_tree_edit_similarity"].iat[0, 1] == 1
    assert (
        count_ast_nodes(normalize_python_source(source, "a.py"))
        == len(list(ast.walk(ast.parse(source)))) + 2
    )


def test_malformed_markdown_reports_error():
    # Large regression fixture; no hardware-dependent time assertion.
    with pytest.raises(AnalysisError, match="sem fechamento"):
        code_tokens("```python\nx=1\n" * 5000, "a.md")


@pytest.mark.parametrize("mapping", [False, True])
def test_hardlink_independence(tmp_path, mapping):
    a, b = groups(tmp_path)
    for name in ["a.py", "b.py"]:
        (b / name).unlink()
        try:
            os.link(a / name, b / name)
        except OSError:
            pytest.skip("Hardlinks unavailable on this filesystem")
    with pytest.raises(AnalysisError, match="arquivos físicos"):
        compare_groups({"A": a, "B": b}, permutations=3) if mapping else compare_groups(
            a, b, permutations=3
        )


@pytest.mark.parametrize("grouped", [False, True])
def test_excel_failure_atomic_and_domain_error(tmp_path, grouped):
    destination = tmp_path / "result.xlsx"
    destination.write_bytes(b"EXISTING RESULT")
    if grouped:
        a, b = groups(tmp_path)
        result = compare_groups({"bad\x01": a, "normal": b}, permutations=3)
    else:
        bad, good = tmp_path / "bad\x01.txt", tmp_path / "good.txt"
        bad.write_text("x")
        good.write_text("y")
        result = analyze([bad, good])
    with pytest.raises(AnalysisError):
        result.to_excel(destination)
    assert destination.read_bytes() == b"EXISTING RESULT"
    assert not list(tmp_path.glob(".codevariability-*"))


def test_normalization_and_attributes_copy(tmp_path):
    result = pair(tmp_path, metrics="jaccard")
    matrix = result.matrices["jaccard"].copy()
    matrix.attrs.update(
        normalization="test_v2",
        dimension="structural_ast",
        adapter_metadata={"cache": {"hits": 1}},
    )
    new = result.with_metric("external", matrix)
    assert new.metadata["normalization"] == "per_metric"
    matrix.attrs["adapter_metadata"]["cache"]["hits"] = 9
    assert new.metadata["external_adapters"]["external"]["cache"]["hits"] == 1


@pytest.mark.parametrize("format", ["json", "csv", "xlsx"])
def test_export_symlink_cannot_overwrite_target(tmp_path, format):
    result = pair(tmp_path)
    sentinel = tmp_path / "sentinel"
    sentinel.write_text("SAFE")
    output = tmp_path / "out"
    output.mkdir()
    name = {
        "json": "analysis.json",
        "csv": "cosine_similarity_matrix.csv",
        "xlsx": "analysis.xlsx",
    }[format]
    target = output / name
    target.symlink_to(sentinel)
    with pytest.raises(AnalysisError, match="link simbólico"):
        result.to_excel(target) if format == "xlsx" else result.export(
            output, formats=format
        )
    assert sentinel.read_text() == "SAFE"


def test_adapter_timeout_reaps_process(tmp_path):
    marker = tmp_path / "started"
    code = f'from pathlib import Path;import time;Path({str(marker)!r}).write_text("STARTED");time.sleep(60)'
    with pytest.raises(AnalysisError, match="ast_timeout"):
        _run_adapter([sys.executable, "-c", code], None, 1)
    assert marker.exists()


def test_adapter_oserror_and_bounded_stderr(tmp_path):
    with pytest.raises(AnalysisError, match="executar"):
        _run_adapter([str(tmp_path / "missing")], None, 1)
    with pytest.raises(AnalysisError, match="LAST ERROR"):
        _run_adapter(
            [
                sys.executable,
                "-c",
                'import sys;sys.stderr.write("x"*100000+"\\nLAST ERROR\\n");sys.exit(7)',
            ],
            None,
            5,
        )


@pytest.mark.parametrize("mapping", [False, True])
def test_provenance(tmp_path, mapping):
    a, b = groups(tmp_path)
    result = (
        compare_groups({"A": a, "B": b}, permutations=3)
        if mapping
        else compare_groups(a, b, permutations=3)
    )
    assert result.metadata["runtime_versions"]["numpy"]
    assert (
        result.metadata["metric_normalizations"]["ast_tree_edit_similarity"]
        == "python_normalized_ast_tree_v2"
    )
    assert len(result.metadata["input_sha256"]) == 4
    result.export(tmp_path / "out")
    assert (
        json.loads((tmp_path / "out/group_comparison.json").read_text())["metadata"][
            "input_sha256"
        ]
        == result.metadata["input_sha256"]
    )


def test_empty_and_comment_fences_agree(tmp_path):
    result = pair(tmp_path, "```python\n\n```", "```python\n# comment\n```", ".md")
    for name in ["lcs", "levenshtein", "ast_tree_edit_similarity"]:
        assert result.matrices[name].iat[0, 1] == 1


def test_immutable_dataset_and_empty_rejection(tmp_path):
    pair(tmp_path)
    mapping = {"a.py": tmp_path / "a.py"}
    dataset = CodeDataset(mapping)
    mapping.clear()
    assert len(dataset.files) == 1
    with pytest.raises(TypeError):
        dataset.files["b.py"] = tmp_path / "b.py"
    with pytest.raises(AnalysisError):
        CodeDataset({})


@pytest.mark.parametrize(
    "matrix",
    [
        [[3, -1], [-1, 3]],
        [[1, 0.5], [0.4, 1]],
        [[0.5, 0], [0, 1]],
        [[1, float("nan")], [float("nan"), 1]],
    ],
)
def test_constructor_checks_invariants(matrix):
    with pytest.raises(AnalysisError):
        AnalysisResult(
            {"external": pd.DataFrame(matrix, index=["a", "b"], columns=["a", "b"])},
            ["a", "b"],
        )


def test_ted_budget_never_returns_approximate_score(tmp_path):
    with pytest.raises(AnalysisError, match="limite max_ted_cells"):
        pair(tmp_path, "x=a+b", "x=a-b", max_ted_cells=1)
    assert (
        pair(tmp_path, "x=a+b", "x=a-b", max_ted_cells=None)
        .matrices["ast_tree_edit_similarity"]
        .iat[0, 1]
        < 1
    )


def test_utf8_bom_supported(tmp_path):
    assert (
        pair(tmp_path, "\ufeffx=1", "x=1")
        .matrices["ast_tree_edit_similarity"]
        .iat[0, 1]
        == 1
    )


def test_cli_ted_budget_and_invalid_excel_label(tmp_path):
    pair(tmp_path, "x=a+b", "x=a-b")
    command = [sys.executable, "-m", "codevariability.cli", "analyze", str(tmp_path)]
    limited = subprocess.run(
        [*command, "--max-ted-cells", "1"], capture_output=True, text=True, timeout=20
    )
    assert limited.returncode == 1
    assert "limite max_ted_cells" in limited.stderr
    assert "Traceback" not in limited.stderr
    unlimited = subprocess.run(
        [*command, "--max-ted-cells", "none"],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert unlimited.returncode == 0
    (tmp_path / "bad\x01.py").write_text("x=1")
    output = tmp_path / "result.xlsx"
    output.write_bytes(b"PREVIOUS")
    invalid = subprocess.run(
        [*command, "--output", str(output), "--extensions", "py"],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert invalid.returncode == 1
    assert "Traceback" not in invalid.stderr
    assert output.read_bytes() == b"PREVIOUS"


@pytest.mark.parametrize(
    "field,value",
    [
        ("metric_ids", []),
        ("metric_normalizations", {"external": []}),
        ("external_adapters", 1),
    ],
)
def test_invalid_constructor_metadata(field, value):
    matrix = pd.DataFrame([[1]], index=["a"], columns=["a"])
    with pytest.raises(AnalysisError):
        AnalysisResult({"external": matrix}, ["a"], {field: value})


@pytest.mark.parametrize(
    "extra",
    [
        {"metric_id": 1},
        {"metadata": []},
        {"metadata": {"normalization": []}},
        {"metadata": {"metric_dimensions": []}},
    ],
)
def test_invalid_adapter_metadata(tmp_path, extra):
    path = tmp_path / "matrix.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "codevariability.matrix.v1",
                "metric": "external",
                "files": ["a"],
                "matrix": [[1]],
                **extra,
            }
        )
    )
    with pytest.raises(AnalysisError):
        load_matrix_json(path)


@pytest.mark.parametrize("label", ["bad\ufffe", "bad\uffff", "bad\ud800"])
def test_excel_xml_invalid_unicode_is_domain_error(tmp_path, label):
    names = [label, "good"]
    matrix = pd.DataFrame([[1, 0.5], [0.5, 1]], index=names, columns=names)
    result = AnalysisResult({"external": matrix}, names)
    destination = tmp_path / "result.xlsx"
    destination.write_bytes(b"PREVIOUS")
    with pytest.raises(AnalysisError):
        result.to_excel(destination)
    assert destination.read_bytes() == b"PREVIOUS"
    assert not list(tmp_path.glob(".codevariability-*"))


@pytest.mark.parametrize("format", ["json", "csv"])
def test_unrepresentable_utf8_export_is_domain_error(tmp_path, format):
    names = ["bad\ud800"]
    result = AnalysisResult(
        {"external": pd.DataFrame([[1]], index=names, columns=names)}, names
    )
    with pytest.raises(AnalysisError):
        result.export(tmp_path, formats=format)
    assert not list(tmp_path.glob(".codevariability-*"))


def test_unrepresentable_metric_and_overflow_timeout(tmp_path):
    result = pair(tmp_path)
    with pytest.raises(AnalysisError):
        result.with_metric("bad\ud800", result.matrices["jaccard"])
    a, b = groups(tmp_path)
    with pytest.raises(AnalysisError):
        compare_groups(a, b, permutations=3, ast_timeout=10**400)
