"""Escape untrusted labels when exporting tables opened by spreadsheet apps."""

from __future__ import annotations

import csv
import re
from pathlib import Path

import pandas as pd

from .exceptions import AnalysisError
from .output import atomic_target


def _safe_cell(value: object, prefix: str) -> object:
    if (
        prefix == "'"
        and isinstance(value, str)
        and re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]", value)
    ):
        raise AnalysisError(
            "O texto exportado contém caracteres incompatíveis com XML do Excel."
        )
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return prefix + value
    return value


def _safe_index(index: pd.Index, prefix: str) -> pd.Index:
    if isinstance(index, pd.MultiIndex):
        return pd.MultiIndex.from_tuples(
            [tuple(_safe_cell(part, prefix) for part in label) for label in index],
            names=[_safe_cell(name, prefix) for name in index.names],
        )
    return pd.Index(
        [_safe_cell(label, prefix) for label in index],
        name=_safe_cell(index.name, prefix),
    )


def _safe_frame(frame: pd.DataFrame, prefix: str) -> pd.DataFrame:
    safe = frame.copy()
    for column in safe.columns:
        if safe[column].dtype == object or pd.api.types.is_string_dtype(
            safe[column].dtype
        ):
            safe[column] = safe[column].map(lambda value: _safe_cell(value, prefix))
    safe.index = _safe_index(safe.index, prefix)
    safe.columns = _safe_index(safe.columns, prefix)
    return safe


def safe_to_csv(frame: pd.DataFrame, path: str | Path, **kwargs: object) -> None:
    kwargs.setdefault("quoting", csv.QUOTE_ALL)
    safe = _safe_frame(frame, "\t")
    with atomic_target(path) as temporary:
        try:
            safe.to_csv(temporary, **kwargs)
        except UnicodeError as exc:
            raise AnalysisError(
                "O texto exportado como CSV deve ser representável em UTF-8."
            ) from exc


def safe_to_excel(
    frame: pd.DataFrame, writer: pd.ExcelWriter, **kwargs: object
) -> None:
    _safe_frame(frame, "'").to_excel(writer, **kwargs)
