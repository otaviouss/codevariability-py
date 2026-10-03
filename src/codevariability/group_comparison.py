"""Permutation-based comparison of two or more independent code collections."""

from __future__ import annotations

import math
import os
import random
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from collections import deque
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import pandas as pd

from .analysis import AnalysisResult, CodeDataset, _dimension_groups
from .exceptions import AnalysisError
from .interop import load_matrix_json
from .output import excel_writer, frame_json, write_json
from .spreadsheet import safe_to_csv, safe_to_excel

AST_TREE_EDIT_METRIC = "ast_tree_edit_similarity"
JAVASCRIPT_EXTENSIONS = {".js", ".jsx", ".ts", ".tsx", ".md", ".markdown"}


def _dataset(value: str | Path | Sequence[str | Path]) -> CodeDataset:
    if isinstance(value, (str, Path)) and Path(value).is_dir():
        return CodeDataset.from_directory(value)
    files = [value] if isinstance(value, (str, Path)) else value
    return CodeDataset.from_files(files)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _within(matrix: pd.DataFrame, indices: Sequence[int]) -> tuple[float, list[float]]:
    values = [float(matrix.iat[i, j]) for i, j in combinations(indices, 2)]
    return _mean(values), values


def _between(matrix: pd.DataFrame, left: Sequence[int], right: Sequence[int]) -> float:
    return _mean([float(matrix.iat[i, j]) for i in left for j in right])


def _group_statistics(
    matrix: pd.DataFrame, left: Sequence[int], right: Sequence[int]
) -> tuple[float, float, float, float, float, list[float], list[float]]:
    within_left, left_values = _within(matrix, left)
    within_right, right_values = _within(matrix, right)
    between = _between(matrix, left, right)
    return (
        within_left,
        within_right,
        between,
        within_left - within_right,
        (within_left + within_right) / 2 - between,
        left_values,
        right_values,
    )


def _array_group_statistics(
    values: Any, left: Sequence[int], right: Sequence[int]
) -> tuple[float, float]:
    """Hot-path statistics over a NumPy-backed matrix without DataFrame indexing."""
    within_left = _mean([float(values[i, j]) for i, j in combinations(left, 2)])
    within_right = _mean([float(values[i, j]) for i, j in combinations(right, 2)])
    between = _mean([float(values[i, j]) for i in left for j in right])
    return within_left - within_right, (within_left + within_right) / 2 - between


def _cliffs_delta(left: Sequence[float], right: Sequence[float]) -> float:
    comparisons = len(left) * len(right)
    if comparisons == 0:
        return 0.0
    greater = sum(a > b for a in left for b in right)
    lower = sum(a < b for a in left for b in right)
    return (greater - lower) / comparisons


def _permutation_p_values(
    matrix: pd.DataFrame,
    size_left: int,
    observed_homogeneity: float,
    observed_separation: float,
    permutations: int,
    random_state: int,
) -> tuple[float, float]:
    rng = random.Random(random_state)
    values = matrix.to_numpy(dtype=float, copy=False)
    population = list(range(len(matrix)))
    extreme_homogeneity = 0
    extreme_separation = 0
    for _ in range(permutations):
        left_set = set(rng.sample(population, size_left))
        left = [index for index in population if index in left_set]
        right = [index for index in population if index not in left_set]
        homogeneity, separation = _array_group_statistics(values, left, right)
        extreme_homogeneity += abs(homogeneity) >= abs(observed_homogeneity)
        extreme_separation += abs(separation) >= abs(observed_separation)
    denominator = permutations + 1
    return (
        (extreme_homogeneity + 1) / denominator,
        (extreme_separation + 1) / denominator,
    )


def _holm(values: Mapping[str, float]) -> dict[str, float]:
    ordered = sorted(values, key=values.get)
    adjusted: dict[str, float] = {}
    previous = 0.0
    total = len(ordered)
    for position, name in enumerate(ordered):
        candidate = min(1.0, (total - position) * values[name])
        previous = max(previous, candidate)
        adjusted[name] = previous
    return adjusted


def _mean_matrix(matrices: Sequence[pd.DataFrame]) -> pd.DataFrame:
    return sum(matrices[1:], matrices[0].copy()) / len(matrices)


def _measure_matrices(analysis: AnalysisResult) -> dict[str, tuple[str, pd.DataFrame]]:
    dimensions = analysis.metadata.get("metric_dimensions", {})
    groups = _dimension_groups(
        analysis.metrics, dimensions if isinstance(dimensions, Mapping) else {}
    )
    measures: dict[str, tuple[str, pd.DataFrame]] = {
        metric: ("metric", matrix) for metric, matrix in analysis.matrices.items()
    }
    dimension_matrices: list[pd.DataFrame] = []
    for dimension, dimension_metrics in groups.items():
        matrix = _mean_matrix([analysis.matrices[name] for name in dimension_metrics])
        measures[f"dimension:{dimension}"] = ("dimension", matrix)
        dimension_matrices.append(matrix)
    measures["overall"] = ("overall", _mean_matrix(dimension_matrices))
    return measures


def _multi_group_statistics(
    matrix: pd.DataFrame, groups: Mapping[str, Sequence[int]]
) -> tuple[dict[str, float], dict[tuple[str, str], float], float, float]:
    within = {name: _within(matrix, indices)[0] for name, indices in groups.items()}
    between = {
        (left, right): _between(matrix, groups[left], groups[right])
        for left, right in combinations(groups, 2)
    }
    mean_within = _mean(list(within.values()))
    mean_between = _mean(list(between.values()))
    homogeneity = sum((value - mean_within) ** 2 for value in within.values())
    return within, between, homogeneity, mean_within - mean_between


def _global_permutation_p_values(
    matrix: pd.DataFrame,
    group_names: Sequence[str],
    group_sizes: Sequence[int],
    observed_homogeneity: float,
    observed_separation: float,
    permutations: int,
    random_state: int,
) -> tuple[float, float]:
    rng = random.Random(random_state)
    values = matrix.to_numpy(dtype=float, copy=False)
    population = list(range(len(matrix)))
    extreme_homogeneity = 0
    extreme_separation = 0
    for _ in range(permutations):
        shuffled = rng.sample(population, len(population))
        offset = 0
        groups: dict[str, list[int]] = {}
        for name, size in zip(group_names, group_sizes, strict=True):
            groups[name] = shuffled[offset:offset + size]
            offset += size
        within = {
            name: _mean([
                float(values[i, j]) for i, j in combinations(indices, 2)
            ])
            for name, indices in groups.items()
        }
        between = [
            _mean([
                float(values[i, j])
                for i in groups[left]
                for j in groups[right]
            ])
            for left, right in combinations(group_names, 2)
        ]
        mean_within = _mean(list(within.values()))
        homogeneity = sum((value - mean_within) ** 2 for value in within.values())
        separation = mean_within - _mean(between)
        extreme_homogeneity += homogeneity >= observed_homogeneity
        extreme_separation += abs(separation) >= abs(observed_separation)
    denominator = permutations + 1
    return (
        (extreme_homogeneity + 1) / denominator,
        (extreme_separation + 1) / denominator,
    )


def _javascript_adapter_command() -> list[str]:
    installed = shutil.which("codevariability-js")
    if installed:
        return [installed]
    raise AnalysisError(
        "A TED JavaScript requer Node.js e o pacote codevariability-js instalado."
    )


def _validate_options(permutations, random_state, include_ast, alpha, progress, ast_timeout, max_ted_cells):
    if not isinstance(permutations, int) or isinstance(permutations, bool) or permutations < 1:
        raise AnalysisError("permutations deve ser um inteiro positivo.")
    if not isinstance(random_state, int) or isinstance(random_state, bool):
        raise AnalysisError("random_state deve ser um inteiro.")
    if not isinstance(include_ast, bool):
        raise AnalysisError("include_ast deve ser True ou False.")
    if not isinstance(alpha, (int, float)) or isinstance(alpha, bool) or not 0 < alpha < 1:
        raise AnalysisError("alpha deve estar estritamente entre 0 e 1.")
    if not isinstance(progress, bool) and not callable(progress):
        raise AnalysisError("progress deve ser True, False ou uma função.")
    try:
        valid_timeout = isinstance(ast_timeout, (int, float)) and not isinstance(ast_timeout, bool) and math.isfinite(ast_timeout) and ast_timeout > 0
    except OverflowError:
        valid_timeout = False
    if not valid_timeout:
        raise AnalysisError("ast_timeout deve ser um número finito e positivo em segundos.")
    if max_ted_cells is not None and (not isinstance(max_ted_cells, int) or isinstance(max_ted_cells, bool) or max_ted_cells < 1):
        raise AnalysisError("max_ted_cells deve ser um inteiro positivo ou None.")


def _independent_files(datasets: Mapping[str, CodeDataset]) -> None:
    seen: set[tuple[int, int]] = set()
    try:
        for dataset in datasets.values():
            for path in dataset.files.values():
                stat = path.stat()
                identity = (stat.st_dev, stat.st_ino)
                if identity in seen:
                    raise AnalysisError("Os grupos independentes não podem compartilhar arquivos físicos, inclusive hardlinks/symlinks.")
                seen.add(identity)
    except OSError as exc:
        raise AnalysisError(f"Não foi possível verificar a identidade física dos arquivos: {exc}.") from exc


def _run_adapter(command: list[str], report, timeout: float) -> None:
    """Drain bounded stderr in a reader while the main thread enforces timeout."""
    lines = deque(maxlen=64)
    reader_errors: list[BaseException] = []
    try:
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                   text=True, encoding="utf-8", errors="replace", start_new_session=os.name == "posix")
    except OSError as exc:
        raise AnalysisError(f"Não foi possível executar o adaptador AST JavaScript: {exc}.") from exc

    def read_stderr():
        try:
            while True:
                # Bound a single line as well as the tail buffer.
                line = process.stderr.readline(4096)
                if not line:
                    break
                message = line.strip()
                if message:
                    lines.append(message)
                    if report is not None:
                        report(message)
        except BaseException as exc:
            reader_errors.append(exc)

    reader = threading.Thread(target=read_stderr, daemon=True)
    reader.start()
    try:
        try:
            return_code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise AnalysisError(f"O adaptador AST JavaScript excedeu ast_timeout={timeout} segundos.") from exc
        reader.join(timeout=1)
        if reader_errors:
            raise AnalysisError(f"Falha ao acompanhar o adaptador AST: {reader_errors[0]}.")
        if return_code:
            raise AnalysisError(f"Falha no adaptador AST JavaScript: {lines[-1] if lines else 'falha desconhecida'}")
    finally:
        # Also stop descendants holding the stderr pipe after the leader exits.
        if os.name == "posix":
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
        elif process.poll() is None:
            process.kill()
        process.wait()
        reader.join(timeout=1)
        process.stderr.close()


def _javascript_ast_matrix(
    files: Mapping[str, Path],
    report: Callable[[str], None] | None = None,
    cache_dir: str | Path | None = None,
    timeout: float = 120.0,
    max_ted_cells: int | None = 2_000_000,
) -> tuple[str, pd.DataFrame]:
    unsupported = [label for label, path in files.items() if path.suffix.lower() not in JAVASCRIPT_EXTENSIONS]
    if unsupported:
        raise AnalysisError(
            "include_ast=True requer somente JavaScript, JSX, TypeScript ou Markdown; "
            f"entradas incompatíveis: {', '.join(unsupported)}."
        )
    with tempfile.TemporaryDirectory(prefix="codevariability-js-ast-") as temporary:
        folder = Path(temporary)
        temporary_files: list[str] = []
        temporary_names: list[str] = []
        for index, path in enumerate(files.values()):
            target = folder / f"{index:06d}{path.suffix.lower()}"
            shutil.copyfile(path, target)
            temporary_files.append(str(target))
            temporary_names.append(target.name)
        output = folder / "ast-tree-edit.json"
        command = [
            *_javascript_adapter_command(),
            "ast",
            *temporary_files,
            "--metric",
            AST_TREE_EDIT_METRIC,
            "--output",
            str(output),
        ]
        if report is not None:
            command.append("--progress")
        if cache_dir is not None:
            command.extend(["--cache-dir", str(Path(cache_dir).resolve())])
        command.extend(["--max-cells", str(max_ted_cells) if max_ted_cells is not None else "none"])
        _run_adapter(command, report, timeout)
        metric, matrix = load_matrix_json(output)
        if metric != AST_TREE_EDIT_METRIC or set(matrix.index) != set(temporary_names):
            raise AnalysisError("O adaptador AST retornou uma métrica ou arquivos incompatíveis.")
        labels = list(files)
        rename = dict(zip(temporary_names, labels, strict=True))
        attrs = deepcopy(matrix.attrs)
        adapter_metadata = attrs.get("adapter_metadata", {})
        hashes = adapter_metadata.get("input_sha256")
        if isinstance(hashes, dict):
            adapter_metadata["input_sha256"] = {rename.get(name, name): value for name, value in hashes.items()}
        matrix = matrix.rename(index=rename, columns=rename).loc[labels, labels]
        matrix.attrs.update(attrs)
        return metric, matrix


@dataclass
class GroupComparisonResult:
    """Results of an independent two-group comparison."""

    summary: pd.DataFrame
    group_names: tuple[str, str]
    files: Mapping[str, list[str]]
    metrics: tuple[str, ...]
    metadata: dict[str, object]

    @property
    def overview(self) -> pd.DataFrame:
        """Compact user-facing view; technical metric rows stay in ``summary``."""
        left, right = self.group_names
        relevant = self.summary[self.summary["kind"].isin(["dimension", "overall"])]
        labels = {
            "dimension:textual": "Textual",
            "dimension:syntactic_token_sequence": "Sintática",
            "dimension:structural_ast": "Estrutural (AST TED)",
            "overall": "Geral",
        }
        rows = []
        alpha = float(self.metadata.get("alpha", 0.05))
        for measure, row in relevant.iterrows():
            difference = float(row["within_difference"])
            rows.append({
                "analysis": labels.get(measure, measure),
                f"within_{left}": row[f"within_{left}"],
                f"within_{right}": row[f"within_{right}"],
                "between_groups": row["between_groups"],
                "more_homogeneous": left if difference > 0 else right if difference < 0 else "tie",
                "homogeneity_p_holm": row["p_value_homogeneity_holm"],
                "separation": row["separation"],
                "separation_p_holm": row["p_value_separation_holm"],
                "evidence_of_separation": bool(
                    row["separation"] > 0 and row["p_value_separation_holm"] < alpha
                ),
            })
        return pd.DataFrame(rows).set_index("analysis")

    @property
    def interpretation(self) -> str:
        """Plain-language interpretation of the overall row."""
        left, right = self.group_names
        row = self.summary.loc["overall"]
        alpha = float(self.metadata.get("alpha", 0.05))
        difference = float(row["within_difference"])
        if difference == 0:
            homogeneity = f"{left} e {right} tiveram a mesma similaridade interna média"
        else:
            leader = left if difference > 0 else right
            homogeneity = f"{leader} apresentou maior similaridade interna média"
        homogeneity_evidence = (
            "com evidência estatística"
            if row["p_value_homogeneity_holm"] < alpha
            else "sem evidência estatística suficiente"
        )
        separation_evidence = (
            "Há evidência de separação entre os grupos"
            if row["separation"] > 0 and row["p_value_separation_holm"] < alpha
            else "Não há evidência suficiente de separação entre os grupos"
        )
        return (
            f"{homogeneity}, {homogeneity_evidence} após correção de Holm "
            f"(p={row['p_value_homogeneity_holm']:.4g}). {separation_evidence} "
            f"(separação={row['separation']:.4f}; "
            f"p={row['p_value_separation_holm']:.4g})."
        )

    def print_report(self) -> None:
        """Print only the compact overview and its cautious interpretation."""
        print(self.overview.round(4))
        print(f"\n{self.interpretation}")

    @property
    def by_metric(self) -> pd.DataFrame:
        return self.summary[self.summary["kind"] == "metric"].copy()

    @property
    def by_dimension(self) -> pd.DataFrame:
        return self.summary[self.summary["kind"].isin(["dimension", "overall"])].copy()

    @property
    def within_groups(self) -> pd.DataFrame:
        left, right = self.group_names
        return self.summary[[f"within_{left}", f"within_{right}", "within_difference"]].copy()

    @property
    def between_groups(self) -> pd.Series:
        return self.summary["between_groups"].copy()

    @property
    def p_values(self) -> pd.DataFrame:
        return self.summary[[
            "p_value_homogeneity",
            "p_value_homogeneity_holm",
            "p_value_separation",
            "p_value_separation_holm",
        ]].copy()

    def export(self, directory: str | Path) -> None:
        output = Path(directory)
        output.mkdir(parents=True, exist_ok=True)
        safe_to_csv(self.summary, output / "group_comparison.csv", index_label="measure")
        payload = {
            "group_names": list(self.group_names),
            "files": self.files,
            "metrics": list(self.metrics),
            "metadata": self.metadata,
            "summary": frame_json(self.summary.reset_index(), "records"),
        }
        write_json(payload, output / "group_comparison.json")

    def to_excel(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with excel_writer(target) as writer:
            safe_to_excel(self.overview, writer, sheet_name="Overview")
            safe_to_excel(pd.DataFrame({"interpretation": [self.interpretation]}), writer,
                sheet_name="Overview", startrow=len(self.overview) + 3, index=False
            )
            safe_to_excel(self.summary, writer, sheet_name="Technical details")
            safe_to_excel(self.by_metric, writer, sheet_name="Metrics")
            safe_to_excel(pd.DataFrame(
                [(group, file) for group, files in self.files.items() for file in files],
                columns=["group", "file"],
            ), writer, sheet_name="Files", index=False)


@dataclass
class MultiGroupComparisonResult:
    """Global and post-hoc results for two or more independent groups."""

    global_test: pd.DataFrame
    pairwise: pd.DataFrame
    within_groups: pd.DataFrame
    between_groups: pd.DataFrame
    group_names: tuple[str, ...]
    files: Mapping[str, list[str]]
    metrics: tuple[str, ...]
    metadata: dict[str, object]

    @property
    def overview(self) -> pd.DataFrame:
        relevant = self.global_test[
            self.global_test["kind"].isin(["dimension", "overall"])
        ]
        labels = {
            "dimension:textual": "Textual",
            "dimension:syntactic_token_sequence": "Sintática",
            "dimension:structural_ast": "Estrutural (AST TED)",
            "overall": "Geral",
        }
        alpha = float(self.metadata.get("alpha", 0.05))
        rows = []
        for measure, row in relevant.iterrows():
            rows.append({
                "analysis": labels.get(measure, measure),
                "homogeneity_p_holm": row["p_value_homogeneity_holm"],
                "separation": row["separation"],
                "separation_p_holm": row["p_value_separation_holm"],
                "some_group_differs": bool(row["p_value_homogeneity_holm"] < alpha),
                "evidence_of_separation": bool(
                    row["separation"] > 0 and row["p_value_separation_holm"] < alpha
                ),
            })
        return pd.DataFrame(rows).set_index("analysis")

    @property
    def pairwise_overview(self) -> pd.DataFrame:
        overall = self.pairwise.xs("overall", level="measure").reset_index()
        alpha = float(self.metadata.get("alpha", 0.05))
        overall["pair"] = overall["group_a"] + " × " + overall["group_b"]
        overall["more_homogeneous"] = overall.apply(
            lambda row: row["group_a"] if row["within_difference"] > 0
            else row["group_b"] if row["within_difference"] < 0 else "tie",
            axis=1,
        )
        overall["evidence_of_separation"] = (
            (overall["separation"] > 0)
            & (overall["p_value_separation_holm"] < alpha)
        )
        return overall.set_index("pair")[[
            "more_homogeneous",
            "within_difference",
            "separation",
            "p_value_homogeneity_holm",
            "p_value_separation_holm",
            "evidence_of_separation",
        ]]

    @property
    def interpretation(self) -> str:
        row = self.global_test.loc["overall"]
        alpha = float(self.metadata.get("alpha", 0.05))
        homogeneity = (
            "Há evidência de que ao menos um grupo possui homogeneidade diferente"
            if row["p_value_homogeneity_holm"] < alpha
            else "Não há evidência suficiente de diferença global de homogeneidade"
        )
        separation = (
            "há evidência de separação global entre os grupos"
            if row["separation"] > 0 and row["p_value_separation_holm"] < alpha
            else "não há evidência suficiente de separação global entre os grupos"
        )
        return (
            f"{homogeneity} (p={row['p_value_homogeneity_holm']:.4g}); {separation} "
            f"(separação={row['separation']:.4f}; "
            f"p={row['p_value_separation_holm']:.4g}). "
            "As comparações pareadas são pós-hoc e devem ser interpretadas em conjunto."
        )

    def print_report(self) -> None:
        print("Teste global:")
        print(self.overview.round(4))
        print("\nComparações pareadas (resultado geral):")
        print(self.pairwise_overview.round(4))
        print(f"\n{self.interpretation}")

    def export(self, directory: str | Path) -> None:
        output = Path(directory)
        output.mkdir(parents=True, exist_ok=True)
        safe_to_csv(self.global_test, output / "global_test.csv", index_label="measure")
        safe_to_csv(self.pairwise, output / "pairwise.csv")
        safe_to_csv(self.within_groups, output / "within_groups.csv", index_label="group")
        safe_to_csv(self.between_groups, output / "between_groups.csv")
        payload = {
            "group_names": list(self.group_names),
            "files": self.files,
            "metrics": list(self.metrics),
            "metadata": self.metadata,
            "global_test": frame_json(self.global_test.reset_index(), "records"),
            "pairwise": frame_json(self.pairwise.reset_index(), "records"),
        }
        write_json(payload, output / "group_comparison.json")

    def to_excel(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with excel_writer(target) as writer:
            safe_to_excel(self.overview, writer, sheet_name="Overview")
            safe_to_excel(self.pairwise_overview,
                writer, sheet_name="Overview", startrow=len(self.overview) + 3
            )
            safe_to_excel(pd.DataFrame({"interpretation": [self.interpretation]}),
                writer,
                sheet_name="Overview",
                startrow=len(self.overview) + len(self.pairwise_overview) + 7,
                index=False,
            )
            safe_to_excel(self.global_test, writer, sheet_name="Global tests")
            safe_to_excel(self.pairwise, writer, sheet_name="Pairwise")
            safe_to_excel(self.within_groups, writer, sheet_name="Within groups")
            safe_to_excel(self.between_groups, writer, sheet_name="Between groups")
            safe_to_excel(pd.DataFrame(
                [(group, file) for group, files in self.files.items() for file in files],
                columns=["group", "file"],
            ), writer, sheet_name="Files", index=False)


def _compare_multiple_groups(
    group_inputs: Mapping[str, str | Path | Sequence[str | Path]],
    *,
    metrics: str | Sequence[str],
    permutations: int,
    random_state: int,
    include_ast: bool,
    alpha: float,
    progress: bool | Callable[[str], None],
    cache_dir: str | Path | None,
    ast_timeout: float,
    max_ted_cells: int | None,
) -> MultiGroupComparisonResult:
    names = tuple(group_inputs)
    if len(names) < 2 or any(not isinstance(name, str) or not name for name in names):
        raise AnalysisError("Informe ao menos dois grupos com nomes não vazios.")
    _validate_options(permutations, random_state, include_ast, alpha, progress, ast_timeout, max_ted_cells)
    report = (
        (lambda message: print(f"[codevariability] {message}", file=sys.stderr, flush=True))
        if progress is True else progress if callable(progress) else None
    )
    if report is not None:
        report(f"Carregando e validando {len(names)} grupos...")

    datasets = {name: _dataset(value) for name, value in group_inputs.items()}
    if any(len(dataset.files) < 2 for dataset in datasets.values()):
        raise AnalysisError("Cada grupo deve conter pelo menos dois arquivos.")
    _independent_files(datasets)

    combined: dict[str, Path] = {}
    public_files: dict[str, list[str]] = {}
    index_groups: dict[str, list[int]] = {}
    for name, dataset in datasets.items():
        public_files[name] = list(dataset.files)
        index_groups[name] = []
        for file_name, path_value in dataset.files.items():
            index_groups[name].append(len(combined))
            combined[f"{name}/{file_name}"] = path_value
    if report is not None:
        report(f"Calculando métricas base para {len(combined)} arquivos...")
    analysis = CodeDataset(combined).analyze(metrics, max_ted_cells=max_ted_cells)
    javascript_ast_added = False
    javascript_ast_cache: object = None
    if include_ast and AST_TREE_EDIT_METRIC not in analysis.metrics:
        if report is not None:
            pairs = len(combined) * (len(combined) - 1) // 2
            report(f"Calculando AST TED JavaScript: {pairs} pares únicos...")
        metric, matrix = _javascript_ast_matrix(combined, report, cache_dir, ast_timeout, max_ted_cells)
        javascript_ast_cache = matrix.attrs.get("adapter_metadata", {}).get("cache")
        analysis = analysis.with_metric(metric, matrix)
        javascript_ast_added = True
    measures = _measure_matrices(analysis)

    global_rows: dict[str, dict[str, object]] = {}
    within_values: dict[str, dict[str, float]] = {name: {} for name in names}
    between_values: dict[tuple[str, str], dict[str, float]] = {
        pair: {} for pair in combinations(names, 2)
    }
    pairwise_rows: dict[tuple[str, str, str], dict[str, object]] = {}
    group_sizes = [len(index_groups[name]) for name in names]
    total_steps = len(measures) * (1 + len(between_values))
    step = 0
    for measure, (kind, matrix) in measures.items():
        step += 1
        if report is not None:
            report(f"Teste global para {measure} ({step}/{total_steps})...")
        within, between, homogeneity, separation = _multi_group_statistics(matrix, index_groups)
        p_homogeneity, p_separation = _global_permutation_p_values(
            matrix, names, group_sizes, homogeneity, separation, permutations, random_state
        )
        global_rows[measure] = {
            "kind": kind,
            "mean_within": _mean(list(within.values())),
            "mean_between": _mean(list(between.values())),
            "homogeneity_statistic": homogeneity,
            "separation": separation,
            "p_value_homogeneity": p_homogeneity,
            "p_value_separation": p_separation,
        }
        for name, value in within.items():
            within_values[name][measure] = value
        for pair, value in between.items():
            between_values[pair][measure] = value

        for left, right in combinations(names, 2):
            step += 1
            if report is not None:
                report(f"Pós-teste {left} × {right}, {measure} ({step}/{total_steps})...")
            stats = _group_statistics(matrix, index_groups[left], index_groups[right])
            within_left, within_right, between_pair, difference, pair_separation, left_pairs, right_pairs = stats
            pair_matrix_indices = [*index_groups[left], *index_groups[right]]
            pair_matrix = matrix.iloc[pair_matrix_indices, pair_matrix_indices]
            p_pair_h, p_pair_s = _permutation_p_values(
                pair_matrix,
                len(index_groups[left]),
                difference,
                pair_separation,
                permutations,
                random_state,
            )
            pairwise_rows[(left, right, measure)] = {
                "kind": kind,
                "within_group_a": within_left,
                "within_group_b": within_right,
                "between_groups": between_pair,
                "within_difference": difference,
                "separation": pair_separation,
                "cliffs_delta_within": _cliffs_delta(left_pairs, right_pairs),
                "p_value_homogeneity": p_pair_h,
                "p_value_separation": p_pair_s,
            }

    global_test = pd.DataFrame.from_dict(global_rows, orient="index")
    global_test.index.name = "measure"
    for source, target in (
        ("p_value_homogeneity", "p_value_homogeneity_holm"),
        ("p_value_separation", "p_value_separation_holm"),
    ):
        global_test[target] = pd.Series(_holm(global_test[source].to_dict()))

    pairwise = pd.DataFrame.from_dict(pairwise_rows, orient="index")
    pairwise.index = pd.MultiIndex.from_tuples(
        pairwise.index, names=["group_a", "group_b", "measure"]
    )
    for source, target in (
        ("p_value_homogeneity", "p_value_homogeneity_holm"),
        ("p_value_separation", "p_value_separation_holm"),
    ):
        raw = {key: float(value) for key, value in pairwise[source].items()}
        pairwise[target] = pd.Series(_holm(raw))

    dimensions = analysis.metadata.get("metric_dimensions", {})
    result = MultiGroupComparisonResult(
        global_test=global_test,
        pairwise=pairwise,
        within_groups=pd.DataFrame.from_dict(within_values, orient="index"),
        between_groups=pd.DataFrame.from_dict(between_values, orient="index").rename_axis(
            index=["group_a", "group_b"]
        ),
        group_names=names,
        files=public_files,
        metrics=analysis.metrics,
        metadata={
            **deepcopy(analysis.metadata),
            "ast_timeout": ast_timeout,
            "method": "multigroup_file_label_permutation_v1",
            "alternative_homogeneity": "upper-tail squared dispersion of within-group means",
            "alternative_separation": "two-sided",
            "group_weighting": "equal",
            "permutations": permutations,
            "random_state": random_state,
            "alpha": float(alpha),
            "global_p_value_correction": "Holm across all reported measures, separately by hypothesis family",
            "pairwise_p_value_correction": "Holm across every pair x measure, separately by hypothesis family",
            "metric_ids": analysis.metadata.get("metric_ids", {}),
            "metric_dimensions": dimensions,
            "overall_aggregation": analysis.metadata.get("overall_aggregation", {}),
            "javascript_ast_included": javascript_ast_added,
            "javascript_ast_cache": javascript_ast_cache,
            "cache_dir": str(Path(cache_dir).resolve()) if cache_dir is not None else None,
        },
    )
    if report is not None:
        report("Comparação multigrupo concluída.")
    return result


def compare_groups(
    group_a: Mapping[str, str | Path | Sequence[str | Path]] | str | Path | Sequence[str | Path],
    group_b: str | Path | Sequence[str | Path] | None = None,
    *,
    metrics: str | Sequence[str] = "all",
    group_names: tuple[str, str] = ("group_a", "group_b"),
    permutations: int = 10_000,
    random_state: int = 42,
    include_ast: bool = False,
    alpha: float = 0.05,
    progress: bool | Callable[[str], None] = False,
    cache_dir: str | Path | None = None,
    ast_timeout: float = 120.0,
    max_ted_cells: int | None = 2_000_000,
) -> GroupComparisonResult | MultiGroupComparisonResult:
    """Compare internal homogeneity and separation of independent groups.

    P-values come from permutation of file-level group labels while preserving
    the original group sizes. Pairwise matrix cells are never permuted as if
    they were independent observations. Pass two inputs for a direct comparison
    or an ordered mapping of names to inputs for a global multigroup analysis.
    """
    if isinstance(group_a, Mapping):
        if group_b is not None:
            raise AnalysisError("Não informe group_b ao usar o formato multigrupo.")
        return _compare_multiple_groups(
            group_a,
            metrics=metrics,
            permutations=permutations,
            random_state=random_state,
            include_ast=include_ast,
            alpha=alpha,
            progress=progress,
            cache_dir=cache_dir,
            ast_timeout=ast_timeout,
            max_ted_cells=max_ted_cells,
        )
    if group_b is None:
        raise AnalysisError("Informe o segundo grupo ou um mapeamento com dois ou mais grupos.")
    if not isinstance(group_names, (tuple, list)) or len(group_names) != 2 or any(not isinstance(name, str) or not name.strip() for name in group_names) or group_names[0] == group_names[1]:
        raise AnalysisError("Informe dois nomes de grupo distintos e não vazios.")
    _validate_options(permutations, random_state, include_ast, alpha, progress, ast_timeout, max_ted_cells)
    report: Callable[[str], None] | None
    if progress is True:
        def report(message: str) -> None:
            print(f"[codevariability] {message}", file=sys.stderr, flush=True)
    elif callable(progress):
        report = progress
    else:
        report = None

    if report is not None:
        report("Carregando e validando os dois grupos...")

    datasets = (_dataset(group_a), _dataset(group_b))
    if any(len(dataset.files) < 2 for dataset in datasets):
        raise AnalysisError("Cada grupo deve conter pelo menos dois arquivos.")
    _independent_files(dict(zip(group_names, datasets, strict=True)))

    combined: dict[str, Path] = {}
    public_files: dict[str, list[str]] = {}
    for group, dataset in zip(group_names, datasets, strict=True):
        public_files[group] = list(dataset.files)
        for name, path in dataset.files.items():
            combined[f"{group}/{name}"] = path
    if report is not None:
        report(f"Calculando métricas base para {len(combined)} arquivos...")
    analysis = CodeDataset(combined).analyze(metrics, max_ted_cells=max_ted_cells)
    javascript_ast_added = False
    javascript_ast_cache: object = None
    if include_ast and AST_TREE_EDIT_METRIC not in analysis.metrics:
        if report is not None:
            pairs = len(combined) * (len(combined) - 1) // 2
            report(f"Calculando AST TED JavaScript: {pairs} pares únicos...")
        metric, matrix = _javascript_ast_matrix(combined, report, cache_dir, ast_timeout, max_ted_cells)
        javascript_ast_cache = matrix.attrs.get("adapter_metadata", {}).get("cache")
        analysis = analysis.with_metric(metric, matrix)
        javascript_ast_added = True

    dimensions = analysis.metadata.get("metric_dimensions", {})
    measures = _measure_matrices(analysis)

    size_left = len(datasets[0].files)
    left_indices = list(range(size_left))
    right_indices = list(range(size_left, len(combined)))
    rows: dict[str, dict[str, object]] = {}
    for measure_index, (measure, (kind, matrix)) in enumerate(measures.items(), start=1):
        if report is not None:
            report(
                f"Permutações para {measure} ({measure_index}/{len(measures)}; "
                f"{permutations} repetições)..."
            )
        within_left, within_right, between, difference, separation, left_values, right_values = _group_statistics(
            matrix, left_indices, right_indices
        )
        p_homogeneity, p_separation = _permutation_p_values(
            matrix,
            size_left,
            difference,
            separation,
            permutations,
            random_state,
        )
        rows[measure] = {
            "kind": kind,
            f"within_{group_names[0]}": within_left,
            f"within_{group_names[1]}": within_right,
            "between_groups": between,
            "within_difference": difference,
            "separation": separation,
            "cliffs_delta_within": _cliffs_delta(left_values, right_values),
            "p_value_homogeneity": p_homogeneity,
            "p_value_separation": p_separation,
        }
    summary = pd.DataFrame.from_dict(rows, orient="index")
    summary.index.name = "measure"
    for source, target in (
        ("p_value_homogeneity", "p_value_homogeneity_holm"),
        ("p_value_separation", "p_value_separation_holm"),
    ):
        adjusted = _holm(summary[source].to_dict())
        summary[target] = pd.Series(adjusted)

    result = GroupComparisonResult(
        summary=summary,
        group_names=tuple(group_names),
        files=public_files,
        metrics=analysis.metrics,
        metadata={
            **deepcopy(analysis.metadata),
            "ast_timeout": ast_timeout,
            "method": "file_label_permutation_v1",
            "alternative": "two-sided",
            "permutations": permutations,
            "random_state": random_state,
            "alpha": float(alpha),
            "javascript_ast_included": javascript_ast_added,
            "javascript_ast_cache": javascript_ast_cache,
            "cache_dir": str(Path(cache_dir).resolve()) if cache_dir is not None else None,
            "p_value_correction": "Holm separately by hypothesis family",
            "metric_ids": analysis.metadata.get("metric_ids", {}),
            "metric_dimensions": dimensions,
            "overall_aggregation": analysis.metadata.get("overall_aggregation", {}),
        },
    )
    if report is not None:
        report("Comparação concluída.")
    return result
