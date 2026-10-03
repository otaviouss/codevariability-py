"""Atomic output files: never follow an existing destination symlink."""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager, suppress
from pathlib import Path

import pandas as pd

from .exceptions import AnalysisError


@contextmanager
def atomic_target(path: str | Path):
    target = Path(path)
    if target.is_symlink():
        raise AnalysisError(
            f"O destino de exportação não pode ser um link simbólico: {target}."
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=".codevariability-", suffix=target.suffix, dir=target.parent
    )
    os.close(descriptor)
    temporary = Path(name)
    try:
        yield temporary
        if target.is_symlink():
            raise AnalysisError(
                f"O destino de exportação tornou-se um link simbólico: {target}."
            )
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def frame_json(frame: pd.DataFrame, orient: str) -> object:
    """Preserve pandas' null conventions, reporting serialization errors uniformly."""
    try:
        return json.loads(frame.to_json(orient=orient))
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise AnalysisError(
            f"A tabela não pode ser serializada como JSON: {exc}."
        ) from exc


def write_json(payload: object, path: str | Path) -> None:
    try:
        content = json.dumps(
            payload, ensure_ascii=False, indent=2, allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise AnalysisError(
            f"O resultado não pode ser serializado como JSON: {exc}."
        ) from exc
    with atomic_target(path) as temporary:
        temporary.write_bytes(content)


@contextmanager
def excel_writer(path: str | Path):
    with atomic_target(path) as temporary:
        # The outer handle closes even if openpyxl cannot save a failed workbook.
        with temporary.open("w+b") as handle:
            writer = pd.ExcelWriter(handle, engine="openpyxl")
            try:
                yield writer
            except BaseException:
                with suppress(Exception):
                    writer.close()
                raise
            else:
                writer.close()
