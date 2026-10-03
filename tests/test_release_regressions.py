"""Permanent regressions for integrity and resource failures found pre-release."""
import itertools
import json
import re
import subprocess
import sys

import pandas as pd
import pytest

from codevariability import AnalysisError, analyze, compare_groups
from codevariability.normalization import _generic_tokens


@pytest.fixture
def groups(tmp_path):
    folders = []
    for name, sources in [("left", ["x=1", "x=1"]), ("right", ["y=2", "y=3"])]:
        folder = tmp_path / name
        folder.mkdir()
        for index, source in enumerate(sources):
            (folder / f"{index}.py").write_text(source, encoding="utf8")
        folders.append(folder)
    return folders


@pytest.mark.parametrize("names", [
    ("alpha", "beta"), ("difference", "right"), ("left", "difference"),
    ("difference", "difference__group"), ("group_b", "group_a"),
    ("jaccard", "metadata"), ("grupo Ω", "組"), ("a/b", "=SUM(A1:A2)"),
    ("between_groups", "kind"), ("p_value_homogeneity", "overall"),
])
def test_group_labels_do_not_share_statistic_keys(groups, tmp_path, names):
    result = compare_groups(*groups, metrics="jaccard", group_names=names, permutations=9)
    left, right = result.within_group_columns
    assert result.group_names == names
    assert len(set(result.summary.columns)) == len(result.summary.columns)
    assert result.summary.loc["jaccard", left] == 1
    assert result.summary.loc["jaccard", right] == pytest.approx(1/3)
    assert result.summary.loc["jaccard", "within_difference"] == pytest.approx(2/3)
    assert result.overview.loc["Textual", left] == 1
    assert result.within_groups.loc["overall", left] == 1
    assert names[0] in result.interpretation
    output = tmp_path / "output"
    result.export(output)
    payload = json.loads((output / "group_comparison.json").read_text())
    assert payload["metadata"]["within_group_columns"] == [left, right]
    assert payload["summary"][0][left] == 1
    result.to_excel(output / "groups.xlsx")
    assert pd.read_excel(output / "groups.xlsx", sheet_name="Technical details").iloc[0][left] == 1


@pytest.mark.parametrize("names", [("same", "same"), ("", "right"), ("left", " "), (None, "right")])
def test_invalid_group_names_still_rejected(groups, names):
    with pytest.raises(AnalysisError):
        compare_groups(*groups, metrics="jaccard", group_names=names, permutations=9)


def test_linear_string_scan_preserves_short_legacy_tokens():
    # Independent old grammar, bounded to short inputs: do not benchmark the
    # vulnerable search on large strings inside the normal test process.
    strings = r'''(?:"(?:\\.|[^"\\])*")|(?:'(?:\\.|[^'\\])*')|(?:`(?:\\.|[^`\\])*`)'''
    from codevariability.normalization import _GENERIC_CODE_TOKEN
    reference = re.compile(strings + "|" + _GENERIC_CODE_TOKEN.pattern.replace("(?x)", ""), re.VERBOSE | re.UNICODE)
    alphabet = ('"', "'", '`', "\\", "\n", "a")
    for size in range(6):
        for characters in itertools.product(alphabet, repeat=size):
            source = "".join(characters)
            assert _generic_tokens(source) == [match.group(0) for match in reference.finditer(source)]
    for source in ("0x12 + 1.2e-3", '"a\\\"b"', "name === value", "'line\ntext'", "'escaped\\\ntext'"):
        assert _generic_tokens(source) == [match.group(0) for match in reference.finditer(source)]


def test_degenerate_string_has_bounded_linear_work():
    # The timeout protects the test runner. The implementation uses a linear
    # scanner, not a timeout or a source-size rejection.
    command = [sys.executable, "-c", 'from codevariability.normalization import code_tokens; s=chr(34)+(chr(92)+chr(34))*100000; assert len(code_tokens(s,"sample.unknown"))==len(s)']
    result = subprocess.run(command, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("multiple", [False, True])
def test_optional_adapter_rejects_a_different_snapshot(groups, monkeypatch, multiple):
    import codevariability.group_comparison as comparison
    def adapter(files, *args):
        labels = list(files)
        matrix = pd.DataFrame(1., index=labels, columns=labels)
        matrix.attrs["adapter_metadata"] = {"input_sha256": dict.fromkeys(labels, "0" * 64)}
        return "ast_tree_edit_similarity", matrix
    monkeypatch.setattr(comparison, "_javascript_ast_matrix", adapter)
    inputs = ({"left": groups[0], "right": groups[1]},) if multiple else tuple(groups)
    with pytest.raises(AnalysisError, match="hashes|mudaram"):
        compare_groups(*inputs, metrics="jaccard", include_ast=True, permutations=9)


@pytest.mark.parametrize("extension,empty", [
    ("py", ""), ("py", "# only a comment\n"), ("py", " \n\t\n"),
    ("md", "```python\n\n```"), ("md", "```py\n# only a comment\n```"),
    ("md", "```python\n\n```\n~~~py\n# comment\n~~~"),
])
def test_empty_python_programs_follow_public_ast_contract(tmp_path, extension, empty):
    paths = [tmp_path / f"{name}.{extension}" for name in ("empty", "also_empty", "code")]
    nonempty = "x=1" if extension == "py" else "```python\nx=1\n```"
    for path, source in zip(paths, (empty, empty, nonempty), strict=True):
        path.write_text(source, encoding="utf-8")
    result = analyze(paths, metrics="ast_tree_edit_similarity")
    matrix = result.matrices["ast_tree_edit_similarity"]
    assert matrix.iat[0, 1] == 1.
    assert matrix.iat[0, 2] == matrix.iat[2, 0] == 0.
    assert matrix.iat[1, 2] == 0.
    assert result.metadata["metric_normalizations"]["ast_tree_edit_similarity"] == "python_normalized_ast_tree_v3"
