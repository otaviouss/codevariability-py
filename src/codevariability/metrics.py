"""Normalized pairwise similarity metrics."""

from __future__ import annotations

from itertools import combinations
from typing import Callable, Mapping, Sequence

import pandas as pd
from rapidfuzz.distance import LCSseq, Levenshtein
from sklearn.feature_extraction.text import CountVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from .ast_tree_edit import (
    AST_TREE_EDIT_METRIC,
    AST_TREE_EDIT_METRIC_ID,
    ast_tree_edit_similarity,
)
from .normalization import code_tokens, text_tokens

DEFAULT_METRICS = ("cosine", "jaccard", "lcs", "levenshtein")
METRICS = (*DEFAULT_METRICS, AST_TREE_EDIT_METRIC)
METRIC_IDS = {
    "cosine": "cosine_bag_of_words_text_v1",
    "jaccard": "jaccard_word_set_text_v1",
    "lcs": "lcs_code_tokens_v1",
    "levenshtein": "levenshtein_code_tokens_v1",
    AST_TREE_EDIT_METRIC: AST_TREE_EDIT_METRIC_ID,
}
METRIC_DIMENSIONS = {
    "cosine": "textual",
    "jaccard": "textual",
    "lcs": "syntactic_token_sequence",
    "levenshtein": "syntactic_token_sequence",
    AST_TREE_EDIT_METRIC: "structural_ast",
}
def _empty_matrix(names: Sequence[str]) -> pd.DataFrame:
    matrix = pd.DataFrame(0.0, index=names, columns=names, dtype=float)
    for name in names:
        matrix.loc[name, name] = 1.0
    return matrix


def cosine(sources: Mapping[str, str]) -> pd.DataFrame:
    names = list(sources)
    corpus = [" ".join(text_tokens(sources[name])) for name in names]
    if not any(corpus):
        return _empty_matrix(names)
    vectors = CountVectorizer(token_pattern=r"(?u)\b\w+\b").fit_transform(corpus)
    return pd.DataFrame(cosine_similarity(vectors), index=names, columns=names)


def jaccard(sources: Mapping[str, str]) -> pd.DataFrame:
    names = list(sources)
    sets = {name: set(text_tokens(text)) for name, text in sources.items()}
    matrix = _empty_matrix(names)
    for left, right in combinations(names, 2):
        union = sets[left] | sets[right]
        value = len(sets[left] & sets[right]) / len(union) if union else 1.0
        matrix.loc[left, right] = matrix.loc[right, left] = value
    return matrix


def lcs(sources: Mapping[str, str]) -> pd.DataFrame:
    names = list(sources)
    processed = {name: code_tokens(text, name) for name, text in sources.items()}
    matrix = _empty_matrix(names)
    for left, right in combinations(names, 2):
        value = LCSseq.normalized_similarity(processed[left], processed[right])
        matrix.loc[left, right] = matrix.loc[right, left] = value
    return matrix


def levenshtein(sources: Mapping[str, str]) -> pd.DataFrame:
    names = list(sources)
    processed = {name: code_tokens(text, name) for name, text in sources.items()}
    matrix = _empty_matrix(names)
    for left, right in combinations(names, 2):
        value = Levenshtein.normalized_similarity(processed[left], processed[right])
        matrix.loc[left, right] = matrix.loc[right, left] = value
    return matrix


CALCULATORS: dict[str, Callable[..., pd.DataFrame]] = {
    "cosine": cosine,
    "jaccard": jaccard,
    "lcs": lcs,
    "levenshtein": levenshtein,
    AST_TREE_EDIT_METRIC: ast_tree_edit_similarity,
}
