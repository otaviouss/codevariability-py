"""Shared validation for public analysis and adapter inputs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from numbers import Real

import numpy as np
import pandas as pd

from .exceptions import AnalysisError

DIMENSION_SCORE_COLUMNS = {
    "textual": "textual_score",
    "syntactic_token_sequence": "syntactic_score",
    "structural_ast": "structural_score",
}
RESERVED_COLUMNS = {
    "file",
    "rank",
    "score",
    "overall_score",
    "overall_dispersion",
    *DIMENSION_SCORE_COLUMNS.values(),
}


def metric_name(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or value in RESERVED_COLUMNS:
        raise AnalysisError(
            "O nome da métrica deve ser texto não vazio e não pode ser uma coluna reservada."
        )
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise AnalysisError(
            "O nome da métrica deve ser representável em UTF-8."
        ) from exc
    return value


def dimensions(metrics: Sequence[str], metadata: Mapping[str, object]) -> None:
    for field in ("metric_ids", "metric_normalizations"):
        values = metadata.get(field, {})
        if not isinstance(values, Mapping) or any(
            not isinstance(value, str) or not value.strip() for value in values.values()
        ):
            raise AnalysisError(
                f"{field} deve mapear métricas para strings não vazias."
            )
    if not isinstance(metadata.get("external_adapters", {}), Mapping):
        raise AnalysisError("external_adapters deve ser um mapeamento.")
    declared = metadata.get("metric_dimensions", {})
    if not isinstance(declared, Mapping):
        raise AnalysisError("metric_dimensions deve ser um mapeamento.")
    columns: dict[str, str] = {}
    for metric in metrics:
        dimension = declared.get(metric, metric)
        if not isinstance(dimension, str) or not dimension.strip():
            raise AnalysisError("A dimensão de cada métrica deve ser texto não vazio.")
        column = DIMENSION_SCORE_COLUMNS.get(dimension, f"{dimension}_score")
        if (
            column in {"file", "rank", "score", "overall_score", "overall_dispersion"}
            or column in metrics
        ):
            raise AnalysisError(
                f"A dimensão '{dimension}' colide com uma coluna reservada ou métrica."
            )
        if column in columns and columns[column] != dimension:
            raise AnalysisError(
                "Dimensões distintas não podem gerar a mesma coluna de score."
            )
        columns[column] = dimension


def similarity_matrix(matrix: object, files: Sequence[str]) -> pd.DataFrame:
    if not isinstance(matrix, pd.DataFrame):
        raise AnalysisError("A matriz de similaridade deve ser um pandas.DataFrame.")
    if not matrix.index.is_unique or not matrix.columns.is_unique:
        raise AnalysisError(
            "Índices e colunas da matriz externa não podem conter nomes duplicados."
        )
    if (
        list(matrix.shape) != [len(files), len(files)]
        or set(matrix.index) != set(files)
        or set(matrix.columns) != set(files)
    ):
        raise AnalysisError(
            "Índices e colunas da matriz externa devem corresponder exatamente aos arquivos analisados."
        )
    if any(
        not isinstance(value, Real) or isinstance(value, (bool, np.bool_))
        for value in matrix.to_numpy().flat
    ):
        raise AnalysisError(
            "A matriz externa deve conter somente números reais, sem booleanos ou strings."
        )
    try:
        aligned = matrix.loc[list(files), list(files)].astype(float).copy(deep=True)
    except (TypeError, ValueError, OverflowError) as exc:
        raise AnalysisError(
            "A matriz externa deve conter somente valores numéricos finitos."
        ) from exc
    values = aligned.to_numpy()
    if not np.isfinite(values).all() or ((values < 0) | (values > 1)).any():
        raise AnalysisError(
            "A matriz externa deve conter somente similaridades numéricas entre 0 e 1."
        )
    if not (values.diagonal() == 1).all():
        raise AnalysisError("A diagonal da matriz externa deve ser igual a 1.")
    if not aligned.equals(aligned.T):
        raise AnalysisError("A matriz externa deve ser simétrica.")
    aligned.attrs = deepcopy(matrix.attrs)
    return aligned
