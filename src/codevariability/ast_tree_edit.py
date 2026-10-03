"""Normalized Python ASTs and ordered tree edit similarity.

The implementation uses the Zhang-Shasha algorithm with unit insertion,
deletion, and relabeling costs.  It deliberately depends only on the Python
standard library; AST identifiers, literal values, locations, comments, and
formatting do not become part of the normalized tree.
"""

from __future__ import annotations

import ast
from array import array
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Mapping

import pandas as pd

from .exceptions import AnalysisError
from .normalization import fenced_code_segments


@dataclass(frozen=True, slots=True)
class NormalizedAstNode:
    """Parser-independent ordered tree node used by the TED implementation."""

    label: str
    children: tuple["NormalizedAstNode", ...] = ()


@dataclass(frozen=True, slots=True)
class CodeFragment:
    code: str
    start_line: int


_IGNORED_AST_FIELDS = {
    "lineno",
    "col_offset",
    "end_lineno",
    "end_col_offset",
    "type_comment",
    "type_ignores",
}

# Concrete names are deliberately ignored wherever CPython represents them as
# strings (Name.id, FunctionDef.name, keyword.arg, alias.name, Attribute.attr,
# pattern capture names, and similar fields). The following scalar fields are
# syntax-bearing rather than identifiers or source metadata.
_STRUCTURAL_SCALAR_FIELDS = ("is_async", "conversion", "level", "simple")


def extract_python_code_fragments(
    source: str, filename: str = "<unknown>"
) -> tuple[CodeFragment, ...]:
    """Preserve .py source; parse closed fences only in Markdown/anonymous input."""
    if Path(filename).suffix.lower() == ".py":
        return (CodeFragment(source, 1),)
    segments = (
        fenced_code_segments(source)
        if Path(filename).suffix.lower() in {".md", ".markdown"}
        or filename == "<unknown>"
        else ()
    )
    if segments:
        if any(
            (segment.language or "").casefold() not in {"", "py", "python"}
            for segment in segments
        ):
            raise AnalysisError("A AST Python requer somente fragmentos Python.")
        return tuple(
            CodeFragment(segment.code, segment.start_line)
            for segment in segments
            if segment.code.strip()
        )
    return (CodeFragment(source, 1),)


def _constant_label(value: object) -> str:
    if value is None:
        category = "None"
    elif value is Ellipsis:
        category = "Ellipsis"
    elif isinstance(value, bool):
        category = "Boolean"
    elif isinstance(value, (int, float, complex)):
        category = "Number"
    elif isinstance(value, str):
        category = "String"
    elif isinstance(value, bytes):
        category = "Bytes"
    else:
        category = type(value).__name__
    return f"Constant:{category}"


def _node_label(node: ast.AST) -> str:
    if isinstance(node, ast.Constant):
        return _constant_label(node.value)
    label = type(node).__name__
    if isinstance(node, ast.MatchSingleton):
        return f"{label}:{_constant_label(node.value).partition(':')[2]}"
    attributes = [
        f"{field}={getattr(node, field)}"
        for field in _STRUCTURAL_SCALAR_FIELDS
        if field in getattr(node, "_fields", ())
    ]
    return ":".join((label, *attributes))


def normalize_ast(node: ast.AST | None) -> NormalizedAstNode | None:
    """Convert a Python AST to an ordered tree without names, values, or metadata."""

    if node is None:
        return None
    if not isinstance(node, ast.AST):
        raise TypeError("normalize_ast() expects an ast.AST node or None")
    # Explicit postorder avoids Python recursion limits on valid long expressions.
    completed: dict[int, NormalizedAstNode] = {}
    pending: list[tuple[ast.AST, bool]] = [(node, False)]
    while pending:
        current, visited = pending.pop()
        children = [
            child
            for name, value in ast.iter_fields(current)
            if name not in _IGNORED_AST_FIELDS
            for child in (
                [value]
                if isinstance(value, ast.AST)
                else value
                if isinstance(value, list)
                else []
            )
            if isinstance(child, ast.AST)
        ]
        if not visited:
            pending.append((current, True))
            pending.extend((child, False) for child in reversed(children))
        else:
            completed[id(current)] = NormalizedAstNode(
                _node_label(current), tuple(completed[id(child)] for child in children)
            )
    return completed[id(node)]


def normalize_python_source(
    source: str, filename: str = "<unknown>"
) -> NormalizedAstNode:
    """Parse all source fragments below a synthetic ordered root."""

    fragments: list[NormalizedAstNode] = []
    for index, fragment in enumerate(extract_python_code_fragments(source, filename)):
        try:
            parsed = ast.parse(fragment.code, filename=filename)
        except SyntaxError as exc:
            source_line = fragment.start_line + (exc.lineno or 1) - 1
            raise AnalysisError(
                f"{filename} (fragmento {index + 1}, linha de origem {source_line}): {exc.msg}."
            ) from exc
        except (ValueError, RecursionError, MemoryError) as exc:
            raise AnalysisError(
                f"{filename}: a fonte excede os limites do parser Python ou é inválida: {exc}."
            ) from exc
        if not parsed.body:
            continue
        normalized = normalize_ast(parsed)
        assert normalized is not None
        fragments.append(NormalizedAstNode("Fragment", (normalized,)))
    return NormalizedAstNode("SyntheticProgram", tuple(fragments))


def count_ast_nodes(tree: NormalizedAstNode | None) -> int:
    """Count normalized nodes iteratively, including the root."""

    if tree is None:
        return 0
    count = 0
    stack = [tree]
    while stack:
        current = stack.pop()
        count += 1
        stack.extend(current.children)
    return count


def _trees_equal(left: NormalizedAstNode, right: NormalizedAstNode) -> bool:
    pending = [(left, right)]
    while pending:
        left_node, right_node = pending.pop()
        if left_node.label != right_node.label or len(left_node.children) != len(
            right_node.children
        ):
            return False
        pending.extend(zip(left_node.children, right_node.children, strict=True))
    return True


def _postorder(
    tree: NormalizedAstNode,
) -> tuple[list[NormalizedAstNode], list[int], list[int]]:
    # Index zero is unused by the one-based algorithm; its placeholder is typed.
    nodes: list[NormalizedAstNode] = [tree]
    leftmost = [0]
    completed: list[tuple[int, int]] = []
    stack: list[tuple[NormalizedAstNode, bool]] = [(tree, False)]
    while stack:
        node, visited = stack.pop()
        if not visited:
            stack.append((node, True))
            stack.extend((child, False) for child in reversed(node.children))
            continue
        index = len(nodes)
        descendant = completed[-len(node.children)][1] if node.children else index
        if node.children:
            del completed[-len(node.children) :]
        nodes.append(node)
        leftmost.append(descendant)
        completed.append((index, descendant))

    last_for_leftmost: dict[int, int] = {}
    for index in range(1, len(nodes)):
        last_for_leftmost[leftmost[index]] = index
    return nodes, leftmost, sorted(last_for_leftmost.values())


def tree_edit_distance(
    left: NormalizedAstNode | None,
    right: NormalizedAstNode | None,
    *,
    max_cells: int | None = 2_000_000,
) -> int:
    """Return ordered Zhang-Shasha TED with unit node-operation costs."""

    if max_cells is not None and (
        not isinstance(max_cells, int) or isinstance(max_cells, bool) or max_cells < 1
    ):
        raise AnalysisError("max_ted_cells deve ser um inteiro positivo ou None.")
    if left is None:
        return count_ast_nodes(right)
    if right is None:
        return count_ast_nodes(left)
    if _trees_equal(left, right):
        return 0

    left_nodes, leftmost_left, left_keyroots = _postorder(left)
    right_nodes, leftmost_right, right_keyroots = _postorder(right)
    right_count = len(right_nodes) - 1
    cells = len(left_nodes) * len(right_nodes)
    if max_cells is not None and cells > max_cells:
        raise AnalysisError(
            f"TED requer {cells} células; limite max_ted_cells={max_cells}. Selecione métricas rápidas ou aumente explicitamente o limite."
        )
    tree_distances = array("I", [0]) * cells

    for left_root in left_keyroots:
        left_start = leftmost_left[left_root]
        rows = left_root - left_start + 2
        for right_root in right_keyroots:
            right_start = leftmost_right[right_root]
            columns = right_root - right_start + 2
            forest = array("I", [0]) * (rows * columns)
            for row in range(1, rows):
                forest[row * columns] = row
            for column in range(1, columns):
                forest[column] = column

            for left_index in range(left_start, left_root + 1):
                row = left_index - left_start + 1
                for right_index in range(right_start, right_root + 1):
                    column = right_index - right_start + 1
                    deletion = forest[(row - 1) * columns + column] + 1
                    insertion = forest[row * columns + column - 1] + 1
                    if (
                        leftmost_left[left_index] == left_start
                        and leftmost_right[right_index] == right_start
                    ):
                        replacement = forest[(row - 1) * columns + column - 1] + (
                            left_nodes[left_index].label
                            != right_nodes[right_index].label
                        )
                        value = min(deletion, insertion, replacement)
                        tree_distances[left_index * (right_count + 1) + right_index] = (
                            value
                        )
                    else:
                        prefix_row = leftmost_left[left_index] - left_start
                        prefix_column = leftmost_right[right_index] - right_start
                        subtree = tree_distances[
                            left_index * (right_count + 1) + right_index
                        ]
                        value = min(
                            deletion,
                            insertion,
                            forest[prefix_row * columns + prefix_column] + subtree,
                        )
                    forest[row * columns + column] = value

    return int(tree_distances[(len(left_nodes) - 1) * (right_count + 1) + right_count])


def tree_edit_similarity(
    left: NormalizedAstNode | None,
    right: NormalizedAstNode | None,
    *,
    max_cells: int | None = 2_000_000,
) -> float:
    """Return ``1 - TED / upper_bound`` using a proven unit-cost bound.

    For two non-empty trees, deleting every non-root node, relabeling the root,
    and inserting every target non-root node costs at most ``n + m - 1``.
    For an empty/non-empty pair the exact upper bound is the non-empty size.
    """

    left_size = count_ast_nodes(left)
    right_size = count_ast_nodes(right)
    if left_size == 0 and right_size == 0:
        return 1.0
    if left_size == 0 or right_size == 0:
        return 0.0
    denominator = left_size + right_size - 1
    distance = tree_edit_distance(left, right, max_cells=max_cells)
    if distance > denominator:
        raise RuntimeError("tree edit distance exceeded its normalization upper bound")
    return 1.0 - distance / denominator


def ast_tree_edit_similarity(
    sources: Mapping[str, str], *, max_cells: int | None = 2_000_000
) -> pd.DataFrame:
    """Calculate the public ``ast_tree_edit_similarity`` pairwise matrix."""

    names = list(sources)
    normalized = {
        name: normalize_python_source(source, name) for name, source in sources.items()
    }
    # A synthetic container with no fragments is an empty program, not a
    # syntax node that should contribute positive overlap with real code.
    trees = {name: tree if tree.children else None for name, tree in normalized.items()}
    matrix = pd.DataFrame(0.0, index=names, columns=names, dtype=float)
    for name in names:
        matrix.loc[name, name] = 1.0
    for left, right in combinations(names, 2):
        value = tree_edit_similarity(trees[left], trees[right], max_cells=max_cells)
        matrix.loc[left, right] = matrix.loc[right, left] = value
    return matrix


AST_TREE_EDIT_METRIC = "ast_tree_edit_similarity"
AST_TREE_EDIT_METRIC_ID = "ast_tree_edit_similarity_v2"
