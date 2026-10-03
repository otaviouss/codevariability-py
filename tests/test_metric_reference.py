"""Similarity scores checked against independent reference calculations."""

import math
import random
import re
import unicodedata
from collections import Counter
from itertools import product

import numpy as np
import pytest

from codevariability import analyze, compare_groups
from codevariability.ast_tree_edit import NormalizedAstNode as Node
from codevariability.ast_tree_edit import tree_edit_distance, tree_edit_similarity


def files(directory, sources):
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, content in sources.items():
        path = directory / name
        path.write_text(content, encoding="utf-8")
        paths.append(path)
    return paths


def pair(directory, left, right, extension, **kwargs):
    return analyze(
        files(directory, {"a" + extension: left, "b" + extension: right}), **kwargs
    )


def words(s):
    return re.findall(r"\b\w+\b", unicodedata.normalize("NFC", s).casefold())


def independent(a, b):
    aa = Counter(words(a))
    bb = Counter(words(b))
    keys = set(aa) | set(bb)
    den = math.sqrt(sum(x * x for x in aa.values()) * sum(x * x for x in bb.values()))
    return (
        sum(aa[k] * bb[k] for k in keys) / den if den else 0,
        len(set(aa) & set(bb)) / len(keys) if keys else 1,
    )


def edit(a, b):
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        curr = [i]
        for j, y in enumerate(b, 1):
            curr.append(min(curr[-1] + 1, prev[j] + 1, prev[j - 1] + (x != y)))
        prev = curr
    return prev[-1]


def lcs(a, b):
    prev = [0] * (len(b) + 1)
    for x in a:
        curr = [0]
        for j, y in enumerate(b, 1):
            curr.append(prev[j - 1] + 1 if x == y else max(prev[j], curr[-1]))
        prev = curr
    return prev[-1]


@pytest.mark.parametrize(
    "a,b",
    list(
        product(
            [
                "",
                "a",
                "a a b",
                "a b c",
                "b c",
                "AÇÃO ação",
                "Straße STRASSE",
                "e\u0301 é",
                "!@#",
            ],
            repeat=2,
        )
    ),
)
def test_independent_text_oracle(tmp_path, a, b):
    r = pair(tmp_path, a, b, ".txt", metrics=["cosine", "jaccard"])
    c, j = independent(a, b)
    assert r.matrices["cosine"].iat[0, 1] == pytest.approx(c)
    assert r.matrices["jaccard"].iat[0, 1] == pytest.approx(j)


@pytest.mark.parametrize(
    "a,b",
    list(product(["", "a", "a b", "a a b", "b a", "a + b", "!=", "λ x"], repeat=2)),
)
def test_independent_sequence_oracle(tmp_path, a, b):
    aa = a.split()
    bb = b.split()
    r = pair(tmp_path, a, b, ".unknown", metrics=["lcs", "levenshtein"])
    den = max(len(aa), len(bb))
    expected_l = lcs(aa, bb) / den if den else 1
    expected_e = 1 - edit(aa, bb) / den if den else 1
    assert r.matrices["lcs"].iat[0, 1] == pytest.approx(expected_l)
    assert r.matrices["levenshtein"].iat[0, 1] == pytest.approx(expected_e)


@pytest.mark.parametrize(
    "left,right,expected",
    [
        (None, None, 0),
        (Node("a"), None, 1),
        (Node("a"), Node("b"), 1),
        (Node("a", (Node("x"),)), Node("a"), 1),
        (Node("a", (Node("x"), Node("y"))), Node("a", (Node("y"), Node("x"))), 2),
        (Node("a", (Node("b", (Node("c"),)),)), Node("a", (Node("c"),)), 1),
    ],
)
def test_manual_ted(left, right, expected):
    assert tree_edit_distance(left, right) == expected
    assert tree_edit_distance(right, left) == expected
    score = tree_edit_similarity(left, right)
    assert 0 <= score <= 1


def test_statistics_and_dimension_oracle(tmp_path):
    r = analyze(files(tmp_path, {"a.py": "x=1", "b.py": "x=2", "c.py": "pass"}))
    for name, m in r.matrices.items():
        values = [m.iat[0, 1], m.iat[0, 2], m.iat[1, 2]]
        row = r.statistics.loc[name]
        assert row["n_pairs"] == 3
        assert row["mean_similarity"] == pytest.approx(sum(values) / 3)
        assert row["std_similarity"] == pytest.approx(np.std(values, ddof=1))
        assert row["q1_similarity"] == pytest.approx(np.quantile(values, 0.25))
        for i, file in enumerate(r.files):
            expected = sum(m.iat[i, j] for j in range(3) if i != j) / 2
            assert r.representativeness.set_index("file").loc[
                file, name
            ] == pytest.approx(expected)
    expected = (
        (r.representativeness["cosine"] + r.representativeness["jaccard"]) / 6
        + (r.representativeness["lcs"] + r.representativeness["levenshtein"]) / 6
        + r.representativeness["ast_tree_edit_similarity"] / 3
    )
    np.testing.assert_allclose(r.representativeness["overall_score"], expected)


def test_permutation_oracle(tmp_path):
    a = files(tmp_path / "a", {"a.txt": "x x", "b.txt": "x y"})
    b = files(tmp_path / "b", {"c.txt": "y z", "d.txt": "z z"})
    r = compare_groups(a, b, metrics="jaccard", permutations=99, random_state=7)
    vals = analyze(a + b, metrics="jaccard").matrices["jaccard"].values

    def stat(left):
        right = [i for i in range(4) if i not in left]
        wa = vals[left[0], left[1]]
        wb = vals[right[0], right[1]]
        bt = sum(vals[i, j] for i in left for j in right) / 4
        return wa - wb, (wa + wb) / 2 - bt

    h, s = stat([0, 1])
    rng = random.Random(7)
    hh = ss = 0
    for _ in range(99):
        left = sorted(rng.sample(range(4), 2))
        ph, ps = stat(left)
        hh += abs(ph) >= abs(h)
        ss += abs(ps) >= abs(s)
    row = r.summary.loc["jaccard"]
    assert row["within_difference"] == pytest.approx(h)
    assert row["separation"] == pytest.approx(s)
    assert row["p_value_homogeneity"] == (hh + 1) / 100
    assert row["p_value_separation"] == (ss + 1) / 100
    assert row["p_value_homogeneity_holm"] == min(1, 3 * row["p_value_homogeneity"])
