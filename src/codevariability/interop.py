"""Interoperability helpers for language-specific analysis adapters."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .exceptions import AnalysisError
from .validation import metric_name, similarity_matrix


def _unique_keys(items):
    result = {}
    for key, value in items:
        if key in result:
            raise AnalysisError(f"Chave JSON duplicada: {key}.")
        result[key] = value
    return result


def load_matrix_json(path: str | Path) -> tuple[str, pd.DataFrame]:
    """Load the JSON matrix schema emitted by a codevariability language adapter."""
    try:
        payload = json.loads(
            Path(path).read_text(encoding="utf-8"), object_pairs_hook=_unique_keys
        )
    except (OSError, UnicodeError, ValueError, RecursionError, TypeError) as exc:
        raise AnalysisError(
            f"Não foi possível ler o JSON de métrica externa: {exc}."
        ) from exc
    if not isinstance(payload, dict):
        raise AnalysisError(
            "JSON de métrica externa inválido ou versão de esquema não suportada."
        )
    required = {"schema_version", "metric", "files", "matrix"}
    missing = required - set(payload)
    if missing or payload.get("schema_version") != "codevariability.matrix.v1":
        raise AnalysisError(
            "JSON de métrica externa inválido ou versão de esquema não suportada."
        )
    files = payload["files"]
    if (
        not isinstance(files, list)
        or not files
        or not all(isinstance(name, str) and name for name in files)
        or len(files) != len(set(files))
    ):
        raise AnalysisError(
            "O JSON de métrica externa deve declarar nomes de arquivo únicos."
        )
    metric = metric_name(payload["metric"])
    matrix = payload["matrix"]
    if (
        not isinstance(metric, str)
        or not metric
        or not isinstance(matrix, list)
        or len(matrix) != len(files)
        or any(not isinstance(row, list) or len(row) != len(files) for row in matrix)
    ):
        raise AnalysisError(
            "O JSON de métrica externa deve conter uma métrica e uma matriz quadrada compatível com os arquivos."
        )
    try:
        frame = similarity_matrix(
            pd.DataFrame(matrix, index=files, columns=files, dtype=object), files
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise AnalysisError(
            "A matriz externa deve conter somente números reais finitos."
        ) from exc
    frame.attrs["metric_id"] = payload.get("metric_id")
    adapter_metadata = payload.get("metadata")
    if adapter_metadata is not None and not isinstance(adapter_metadata, dict):
        raise AnalysisError("Os metadados do adaptador devem ser um objeto JSON.")
    if frame.attrs["metric_id"] is not None and (
        not isinstance(frame.attrs["metric_id"], str)
        or not frame.attrs["metric_id"].strip()
    ):
        raise AnalysisError("metric_id deve ser texto não vazio.")
    if isinstance(adapter_metadata, dict):
        frame.attrs["adapter_metadata"] = adapter_metadata
        frame.attrs["normalization"] = adapter_metadata.get("normalization")
        dimensions = adapter_metadata.get("metric_dimensions")
        if isinstance(dimensions, dict):
            frame.attrs["dimension"] = dimensions.get(metric)
        elif dimensions is not None:
            raise AnalysisError("metric_dimensions deve ser um objeto JSON.")
        for attribute in ("normalization", "dimension"):
            value = frame.attrs.get(attribute)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise AnalysisError(f"{attribute} deve ser texto não vazio.")
    return metric, frame
