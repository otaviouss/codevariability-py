"""Dataset loading, pairwise analysis, summaries, and result export."""

from __future__ import annotations

import hashlib
import json
import math
import platform
import re
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Iterable, Mapping, Sequence

import numpy as np
import pandas as pd
import pygments
import rapidfuzz
import sklearn

from .exceptions import AnalysisError
from .metrics import CALCULATORS, METRIC_DIMENSIONS, METRIC_IDS, METRICS
from .normalization import CODE_TOKENIZATION, TEXT_NORMALIZATION, uses_python_code
from .output import excel_writer, frame_json, write_json
from .spreadsheet import safe_to_csv, safe_to_excel
from .validation import (
    DIMENSION_SCORE_COLUMNS,
    dimensions,
    metric_name,
    similarity_matrix,
)

LEGACY_AST_METRIC = "ast_node_type_multiset_jaccard"


def _dimension_groups(
    metrics: Iterable[str], metric_dimensions: Mapping[str, object]
) -> dict[str, list[str]]:
    """Group aggregate metrics, preferring TED over the legacy AST baseline."""
    metric_list = list(metrics)
    groups: dict[str, list[str]] = {}
    for metric in metric_list:
        dimension = str(metric_dimensions.get(metric, metric))
        if (
            dimension == "structural_ast"
            and "ast_tree_edit_similarity" in metric_list
            and metric == LEGACY_AST_METRIC
        ):
            continue
        groups.setdefault(dimension, []).append(metric)
    return groups


def _aggregation_metadata(
    metrics: Iterable[str], metric_dimensions: Mapping[str, object]
) -> dict[str, object]:
    return {
        "method": "equal_weighted_available_dimensions_v1",
        "dimension_metrics": _dimension_groups(metrics, metric_dimensions),
    }


def _natural_key(value: str) -> list[object]:
    return [int(part) if part.isdecimal() else part.lower() for part in re.split(r"(\d+)", value)]


def _metric_filename(metric: str) -> str:
    """Keep external metric names inside the export directory."""
    if re.fullmatch(r"[A-Za-z0-9_-]{1,80}", metric):
        return metric
    label = re.sub(r"[^A-Za-z0-9_-]+", "_", metric).strip("_")[:60] or "metric"
    digest = hashlib.sha256(metric.encode("utf-8")).hexdigest()[:12]
    return f"{label}_{digest}"


def _pair_values(matrix: pd.DataFrame) -> list[float]:
    return [float(matrix.iloc[row, column]) for row in range(len(matrix)) for column in range(row + 1, len(matrix))]


def _summary(matrix: pd.DataFrame) -> dict[str, float | int | None]:
    values = pd.Series(_pair_values(matrix), dtype=float)
    if values.empty:
        return {"n_files": len(matrix), "n_pairs": 0, "mean_similarity": None, "median_similarity": None,
                "std_similarity": None, "min_similarity": None, "max_similarity": None,
                "q1_similarity": None, "q3_similarity": None, "mean_variability": None}
    mean = float(values.mean())
    return {"n_files": len(matrix), "n_pairs": len(values), "mean_similarity": mean,
            "median_similarity": float(values.median()), "std_similarity": float(values.std(ddof=1)) if len(values) > 1 else 0.0,
            "min_similarity": float(values.min()), "max_similarity": float(values.max()),
            "q1_similarity": float(values.quantile(.25)), "q3_similarity": float(values.quantile(.75)),
            "mean_variability": 1 - mean}


@dataclass(frozen=True)
class CodeDataset:
    """An immutable collection of UTF-8 documents identified by unique basenames."""
    files: Mapping[str, Path]

    def __post_init__(self) -> None:
        if not isinstance(self.files, Mapping) or not self.files or any(not isinstance(name, str) or not name for name in self.files):
            raise AnalysisError("O dataset deve conter arquivos com nomes textuais únicos e não vazios.")
        try:
            paths = {name: Path(path) for name, path in self.files.items()}
        except (TypeError, ValueError) as exc:
            raise AnalysisError("Os caminhos dos arquivos devem ser strings ou Paths válidos.") from exc
        object.__setattr__(self, "files", MappingProxyType(paths))

    @classmethod
    def from_directory(cls, directory: str | Path, extensions: Iterable[str] | None = None) -> "CodeDataset":
        try:
            folder = Path(directory)
        except (TypeError, ValueError) as exc:
            raise AnalysisError("O diretório deve ser um caminho válido.") from exc
        if not folder.exists():
            raise AnalysisError(f"Diretório não encontrado: {folder}.")
        if not folder.is_dir():
            raise AnalysisError(f"O caminho informado não é um diretório: {folder}.")
        if isinstance(extensions, str):
            extensions = [extensions]
        try:
            allowed: set[str] | None = None
            if extensions is not None:
                allowed = set()
                for extension in extensions:
                    if not isinstance(extension, str) or not extension.strip():
                        raise AnalysisError("Extensões devem ser strings não vazias.")
                    extension = extension.strip().lower()
                    allowed.add(extension if extension.startswith(".") else f".{extension}")
        except TypeError as exc:
            raise AnalysisError("extensions deve ser uma string ou coleção de strings.") from exc
        try:
            files = sorted(
                (item for item in folder.iterdir() if item.is_file() and (allowed is None or item.suffix.lower() in allowed)),
                key=lambda item: (_natural_key(item.name), item.name),
            )
        except OSError as exc:
            raise AnalysisError(f"Não foi possível listar o diretório {folder}: {exc}.") from exc
        if not files:
            raise AnalysisError(f"Nenhum arquivo textual encontrado em {folder}.")
        return cls({item.name: item for item in files})

    @classmethod
    def from_files(cls, files: Sequence[str | Path]) -> "CodeDataset":
        if isinstance(files, (str, Path)):
            files = [files]
        try:
            paths = [Path(item) for item in files]
        except (TypeError, ValueError) as exc:
            raise AnalysisError("Informe uma coleção de caminhos de arquivos válidos.") from exc
        if not paths:
            raise AnalysisError("Informe ao menos um arquivo textual.")
        names = [item.name for item in paths]
        if len(set(names)) != len(names):
            raise AnalysisError("Os nomes-base dos arquivos devem ser únicos.")
        invalid = [str(item) for item in paths if not item.is_file()]
        if invalid:
            raise AnalysisError(f"Arquivo(s) não encontrado(s) ou inválido(s): {', '.join(invalid)}.")
        return cls(dict(zip(names, paths, strict=True)))

    def analyze(self, metrics: str | Sequence[str] = "all", *, max_ted_cells: int | None = 2_000_000) -> "AnalysisResult":
        if max_ted_cells is not None and (not isinstance(max_ted_cells, int) or isinstance(max_ted_cells, bool) or max_ted_cells < 1):
            raise AnalysisError("max_ted_cells deve ser um inteiro positivo ou None.")
        try:
            raw_sources = {name: path.read_bytes() for name, path in self.files.items()}
            sources = {name: raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n") for name, raw in raw_sources.items()}
        except (OSError, UnicodeError) as exc:
            raise AnalysisError(f"Não foi possível ler os arquivos textuais como UTF-8: {exc}.") from exc

        def applicable(metric: str) -> bool:
            if metric == "ast_tree_edit_similarity":
                return all(uses_python_code(sources[name], name) for name in self.files)
            return True

        if isinstance(metrics, str):
            selected = [metric for metric in METRICS if applicable(metric)] if metrics == "all" else [metrics]
        else:
            try:
                selected = list(metrics)
            except TypeError as exc:
                raise AnalysisError("metrics deve ser uma string ou coleção de nomes de métricas.") from exc
        if any(not isinstance(metric, str) or not metric for metric in selected):
            raise AnalysisError("Os nomes das métricas devem ser strings não vazias.")
        selected = list(dict.fromkeys(selected))
        if not selected:
            raise AnalysisError("Informe ao menos uma métrica.")
        unknown = set(selected) - set(METRICS)
        if unknown:
            raise AnalysisError(f"Métrica(s) desconhecida(s): {', '.join(sorted(unknown))}.")
        incompatible = [metric for metric in selected if not applicable(metric)]
        if incompatible:
            raise AnalysisError(
                "Métrica(s) incompatível(is) com as entradas analisadas: "
                f"{', '.join(incompatible)}."
            )
        matrices = {}
        for metric in selected:
            matrix = CALCULATORS[metric](sources, max_cells=max_ted_cells) if metric == "ast_tree_edit_similarity" else CALCULATORS[metric](sources)
            # The exact TED normalization has a proven bound and must not rely
            # on clipping to hide an invalid value. Other normalized metrics
            # retain a floating-point guard.
            if metric == "ast_tree_edit_similarity":
                if matrix.isna().any().any() or ((matrix < 0) | (matrix > 1)).any().any():
                    raise AnalysisError("A similaridade TED calculada ficou fora do intervalo [0, 1].")
            else:
                matrix = matrix.clip(lower=0.0, upper=1.0)
            # Set the diagonal explicitly to avoid floating-point drift.
            for name in matrix.index:
                matrix.loc[name, name] = 1.0
            matrices[metric] = matrix
        normalizations = {
            metric: (
                "python_normalized_ast_tree_v3" if metric == "ast_tree_edit_similarity"
                else TEXT_NORMALIZATION if metric in {"cosine", "jaccard"}
                else CODE_TOKENIZATION
            )
            for metric in selected
        }
        metric_dimensions = {metric: METRIC_DIMENSIONS[metric] for metric in selected}
        return AnalysisResult(matrices=matrices, files=list(self.files), metadata={
            "metrics": list(selected),
            "normalization": next(iter(set(normalizations.values()))) if len(set(normalizations.values())) == 1 else "per_metric",
            "metric_normalizations": normalizations,
            "metric_ids": {metric: METRIC_IDS[metric] for metric in selected},
            "metric_dimensions": metric_dimensions,
            "overall_aggregation": _aggregation_metadata(selected, metric_dimensions),
            "runtime_versions": {
                "python": platform.python_version(),
                "numpy": np.__version__,
                "pandas": pd.__version__,
                "pygments": pygments.__version__,
                "rapidfuzz": rapidfuzz.__version__,
                "scikit_learn": sklearn.__version__,
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
            "input_sha256": {name: hashlib.sha256(raw).hexdigest() for name, raw in raw_sources.items()},
            "max_ted_cells": max_ted_cells,
        })


@dataclass
class AnalysisResult:
    matrices: Mapping[str, pd.DataFrame]
    files: list[str]
    metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.files, (list, tuple)) or not self.files or any(not isinstance(name, str) or not name for name in self.files) or len(set(self.files)) != len(self.files):
            raise AnalysisError("O resultado requer uma coleção não vazia de nomes de arquivos únicos.")
        if not isinstance(self.matrices, Mapping) or not self.matrices:
            raise AnalysisError("O resultado requer ao menos uma matriz de similaridade.")
        if not isinstance(self.metadata, Mapping):
            raise AnalysisError("metadata deve ser um mapeamento.")
        self.files = list(self.files)
        self.metadata = deepcopy(dict(self.metadata))
        for name in self.matrices:
            metric_name(name)
        dimensions(list(self.matrices), self.metadata)
        self.matrices = {name: similarity_matrix(matrix, self.files) for name, matrix in self.matrices.items()}

    @property
    def metrics(self) -> tuple[str, ...]:
        """Public metric names, in the order in which they were calculated."""
        return tuple(self.matrices)

    @property
    def statistics(self) -> pd.DataFrame:
        """One row per metric, calculated from the unique pairs ``i < j``."""
        result = pd.DataFrame.from_dict(
            {metric: _summary(matrix) for metric, matrix in self.matrices.items()},
            orient="index",
        )
        result.index.name = "metric"
        return result

    @staticmethod
    def _metric_ranking(matrix: pd.DataFrame) -> pd.DataFrame:
        if len(matrix) == 1:
            values = pd.Series([1.0], index=matrix.index)
        else:
            diagonal = pd.Series(matrix.to_numpy().diagonal(), index=matrix.index)
            values = (matrix.sum(axis=1) - diagonal) / (len(matrix) - 1)
        result = pd.DataFrame({"file": values.index, "score": values.values})
        result = result.sort_values(["score", "file"], ascending=[False, True], ignore_index=True)
        result.insert(0, "rank", range(1, len(result) + 1))
        return result

    @property
    def rankings_by_metric(self) -> dict[str, pd.DataFrame]:
        """Rank files by mean similarity to every other file, per metric."""
        return {metric: self._metric_ranking(matrix) for metric, matrix in self.matrices.items()}

    @staticmethod
    def _row_dispersion(matrix: pd.DataFrame) -> pd.Series:
        """Sample standard deviation of each file's similarity to every other file.

        A low value means the file is about equally similar to everyone; a
        high value means its row average is driven disproportionately by a
        few close (or a few distant) files rather than by broad centrality.
        Returns 0.0 wherever fewer than two off-diagonal values exist, the
        same convention used by :func:`_summary` for ``std_similarity``.
        """
        if len(matrix) <= 2:
            return pd.Series(0.0, index=matrix.index)
        masked = matrix.to_numpy(dtype=float).copy()
        np.fill_diagonal(masked, np.nan)
        return pd.DataFrame(masked, index=matrix.index, columns=matrix.columns).std(axis=1, ddof=1)

    @property
    def representativeness(self) -> pd.DataFrame:
        """Per-file metric, dimension and overall representativeness scores.

        Every metric score is the mean similarity from that file to all other
        files. Metrics are first averaged within each methodological dimension;
        ``overall_score`` is then the unweighted mean of the available
        dimension scores. When TED is present, the legacy AST node-frequency
        metric remains visible but does not enter the structural score.

        ``overall_dispersion`` is the sample standard deviation of the same
        file's row in the equally-weighted dimension-average matrix underlying
        ``overall_score`` (see :meth:`_row_dispersion`). It diagnoses whether a
        high ``overall_score`` reflects broad similarity to the whole set or is
        concentrated on a few close neighbours, which a mean alone cannot show.
        """
        result = pd.DataFrame({"file": self.files})
        for metric, ranking in self.rankings_by_metric.items():
            scores = ranking.set_index("file")["score"]
            result[metric] = result["file"].map(scores)

        dimensions = self.metadata.get("metric_dimensions", {})
        groups = _dimension_groups(
            self.metrics, dimensions if isinstance(dimensions, Mapping) else {}
        )

        dimension_columns: list[str] = []
        dimension_matrices: list[pd.DataFrame] = []
        for dimension, metrics in groups.items():
            column = DIMENSION_SCORE_COLUMNS.get(dimension, f"{dimension}_score")
            result[column] = result[metrics].mean(axis=1)
            dimension_columns.append(column)
            combined = sum(self.matrices[metric] for metric in metrics) / len(metrics)
            dimension_matrices.append(combined)
        result["overall_score"] = result[dimension_columns].mean(axis=1)
        if dimension_matrices:
            overall_matrix = sum(dimension_matrices) / len(dimension_matrices)
            dispersion = self._row_dispersion(overall_matrix)
            result["overall_dispersion"] = result["file"].map(dispersion)
        return result

    @property
    def ranking(self) -> pd.DataFrame:
        """Overall deterministic ranking, most representative first."""
        result = self.representativeness[["file", "overall_score"]].rename(
            columns={"overall_score": "score"}
        )
        result = result.sort_values(["score", "file"], ascending=[False, True], ignore_index=True)
        result.insert(0, "rank", range(1, len(result) + 1))
        return result

    @property
    def most_representative(self) -> dict[str, str | float | int]:
        row = self.ranking.iloc[0]
        return {"file": str(row["file"]), "score": float(row["score"]), "rank": int(row["rank"])}

    @property
    def most_distinct(self) -> dict[str, str | float | int]:
        row = self.ranking.iloc[-1]
        return {"file": str(row["file"]), "score": float(row["score"]), "rank": int(row["rank"])}

    def medoid(self, metric: str) -> dict[str, str | float | int]:
        self._matrix(metric)
        row = self.rankings_by_metric[metric].iloc[0]
        return {"file": str(row["file"]), "mean_similarity": float(row["score"]),
                "mean_variability": 1 - float(row["score"]), "rank": int(row["rank"]), "metric": metric}

    def composite(self, weights: Mapping[str, float] | str = "equal") -> pd.DataFrame:
        """Return an explicitly exploratory weighted composite similarity matrix."""
        if not self.matrices:
            raise AnalysisError("Não há matrizes para compor.")
        if weights == "equal":
            normalized = {metric: 1 / len(self.matrices) for metric in self.matrices}
        elif isinstance(weights, Mapping):
            unknown = set(weights) - set(self.matrices)
            missing = set(self.matrices) - set(weights)
            try:
                numeric_weights = {metric: float(value) for metric, value in weights.items()}
                total = sum(numeric_weights.values())
            except (TypeError, ValueError, OverflowError) as exc:
                raise AnalysisError("Pesos devem ser números finitos.") from exc
            if unknown or missing or not math.isfinite(total) or total <= 0 or any(not math.isfinite(value) or value < 0 for value in numeric_weights.values()):
                raise AnalysisError("Pesos devem ser finitos, não negativos, somar valor positivo e incluir exatamente as métricas analisadas.")
            normalized = {metric: value / total for metric, value in numeric_weights.items()}
        else:
            raise AnalysisError("Use weights='equal' ou um dicionário de pesos.")
        composite = sum((self.matrices[metric] * weight for metric, weight in normalized.items()))
        composite.attrs["kind"] = "exploratory_composite_similarity"
        composite.attrs["weights"] = normalized
        return composite

    def with_metric(self, metric: str, matrix: pd.DataFrame) -> "AnalysisResult":
        """Return a copy augmented by a normalized external metric matrix.

        Language adapters use a readable public metric name, e.g.
        ``ast_node_type_multiset_jaccard``; their interchange metadata records
        the versioned internal identifier.
        """
        metric_name(metric)
        if metric in self.matrices:
            raise AnalysisError("A métrica externa deve ter um nome novo e não vazio.")
        aligned = similarity_matrix(matrix, self.files)
        metadata = dict(self.metadata)
        metadata["metrics"] = [*self.metrics, metric]
        for metadata_field, attribute in (
            ("metric_ids", "metric_id"),
            ("metric_normalizations", "normalization"),
            ("metric_dimensions", "dimension"),
        ):
            existing = metadata.get(metadata_field, {})
            if not isinstance(existing, Mapping):
                raise AnalysisError(f"metadata.{metadata_field} deve ser um mapeamento.")
            values = dict(existing)
            value = matrix.attrs.get(attribute)
            if value is not None:
                if not isinstance(value, str) or not value.strip():
                    raise AnalysisError(f"O atributo '{attribute}' da métrica externa deve ser texto não vazio.")
                values[metric] = value
            metadata[metadata_field] = values
        adapter_metadata = matrix.attrs.get("adapter_metadata")
        if isinstance(adapter_metadata, dict):
            existing_adapters = metadata.get("external_adapters", {})
            if not isinstance(existing_adapters, Mapping):
                raise AnalysisError("metadata.external_adapters deve ser um mapeamento.")
            adapters = dict(existing_adapters)
            adapters[metric] = adapter_metadata
            metadata["external_adapters"] = adapters
        dimensions = metadata.get("metric_dimensions", {})
        metadata["overall_aggregation"] = _aggregation_metadata(
            [*self.metrics, metric], dimensions if isinstance(dimensions, Mapping) else {}
        )
        normalizations = metadata.get("metric_normalizations", {})
        if not isinstance(normalizations, Mapping):
            raise AnalysisError("metadata.metric_normalizations deve ser um mapeamento.")
        unique = set(normalizations.values())
        metadata["normalization"] = next(iter(unique)) if len(unique) == 1 and len(normalizations) == len(self.metrics) + 1 else "per_metric"
        return AnalysisResult(matrices={**self.matrices, metric: aligned}, files=list(self.files), metadata=metadata)

    def export(self, directory: str | Path, formats: str | Sequence[str] = ("json", "csv")) -> None:
        try:
            requested = {formats} if isinstance(formats, str) else set(formats)
        except TypeError as exc:
            raise AnalysisError("formats deve ser uma string ou coleção de formatos.") from exc
        if not requested:
            raise AnalysisError("Informe ao menos um formato de exportação.")
        if not requested <= {"json", "csv"}:
            raise AnalysisError("Formatos suportados nesta versão: json, csv.")
        output = Path(directory)
        output.mkdir(parents=True, exist_ok=True)
        if "csv" in requested:
            for metric, matrix in self.matrices.items():
                safe_to_csv(matrix, output / f"{_metric_filename(metric)}_similarity_matrix.csv", index_label="file")
            safe_to_csv(self.statistics, output / "statistics.csv", index_label="metric")
            safe_to_csv(self.representativeness, output / "representativeness.csv", index=False)
            safe_to_csv(self.ranking, output / "ranking.csv", index=False)
            for metric, ranking in self.rankings_by_metric.items():
                safe_to_csv(ranking, output / f"{_metric_filename(metric)}_ranking.csv", index=False)
        if "json" in requested:
            payload = {
                "metadata": self.metadata,
                "files": self.files,
                "metrics": list(self.metrics),
                "statistics": frame_json(self.statistics, "index"),
                "representativeness": frame_json(self.representativeness, "records"),
                "rankings_by_metric": {
                    metric: frame_json(ranking, "records")
                    for metric, ranking in self.rankings_by_metric.items()
                },
                "ranking": frame_json(self.ranking, "records"),
                "most_representative": self.most_representative,
                "most_distinct": self.most_distinct,
                "matrices": {metric: matrix.to_dict() for metric, matrix in self.matrices.items()},
            }
            write_json(payload, output / "analysis.json")

    def to_excel(self, path: str | Path) -> None:
        """Export the already calculated analysis to one Excel workbook."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        summary = pd.DataFrame({
            "field": ["number_of_files", "metrics_used", "most_representative_file",
                      "most_distinct_file", "representative_overall_score"],
            "value": [len(self.files), ", ".join(self.metrics), self.most_representative["file"],
                      self.most_distinct["file"], self.most_representative["score"]],
        })
        metadata = pd.DataFrame({
            "field": list(self.metadata),
            "value": [
                json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list)) else value
                for value in self.metadata.values()
            ],
        })
        used_sheets = {"summary", "metadata", "statistics", "representativeness", "ranking"}
        try:
            with excel_writer(target) as writer:
                safe_to_excel(summary, writer, sheet_name="Summary", index=False)
                safe_to_excel(metadata, writer, sheet_name="Metadata", index=False)
                safe_to_excel(self.representativeness, writer, sheet_name="Representativeness", index=False)
                safe_to_excel(self.ranking, writer, sheet_name="Ranking", index=False)
                safe_to_excel(self.statistics, writer, sheet_name="Statistics")
                for metric, matrix in self.matrices.items():
                    sheet = self._excel_sheet_name(metric, used_sheets)
                    safe_to_excel(matrix, writer, sheet_name=sheet, index_label="file")
        except (ImportError, ModuleNotFoundError) as exc:
            raise AnalysisError("A exportação Excel requer a dependência openpyxl.") from exc

    @staticmethod
    def _excel_sheet_name(metric: str, used: set[str]) -> str:
        base = re.sub(r"[\\/*?:\[\]]", "_", metric)[:31] or "Metric"
        candidate = base
        suffix = 2
        while candidate.lower() in used:
            marker = f"_{suffix}"
            candidate = f"{base[:31 - len(marker)]}{marker}"
            suffix += 1
        used.add(candidate.lower())
        return candidate

    def _matrix(self, metric: str) -> pd.DataFrame:
        try:
            return self.matrices[metric]
        except KeyError as exc:
            raise AnalysisError(f"A métrica '{metric}' não foi calculada.") from exc


def analyze(
    files_or_directory: str | Path | Sequence[str | Path],
    metrics: str | Sequence[str] = "all",
    *,
    extensions: Iterable[str] | str | None = None,
    max_ted_cells: int | None = 2_000_000,
) -> AnalysisResult:
    """Analyze code files from a directory or an explicit sequence of paths."""
    dataset = CodeDataset.from_directory(files_or_directory, extensions=extensions) if isinstance(files_or_directory, (str, Path)) and Path(files_or_directory).is_dir() else CodeDataset.from_files([files_or_directory] if isinstance(files_or_directory, (str, Path)) else files_or_directory)
    return dataset.analyze(metrics=metrics, max_ted_cells=max_ted_cells)
