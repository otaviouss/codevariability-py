# Python API

Import the public API from `codevariability`. Submodules implement the metrics
and validation; they are not a separately versioned extension API.

## Analysis

```text
analyze(files_or_directory, metrics="all", *, extensions=None,
        max_ted_cells=2_000_000) -> AnalysisResult
```

`files_or_directory` is a directory, an individual file, or a sequence of file
paths. `extensions` filters directory inputs only. `metrics` accepts one name,
a sequence, or `"all"`. Duplicate selected names are deduplicated.

For repeated use, `CodeDataset.from_directory(directory, extensions=None)`
and `CodeDataset.from_files(paths)` create an immutable mapping of labels to
paths. `dataset.analyze(metrics="all", max_ted_cells=2_000_000)` reads the
current file contents and returns a new result.

| AnalysisResult member | Return value or action |
| --- | --- |
| `files` / `metrics` | File labels / tuple of selected metric names. |
| `matrices` | Mapping from metric name to a square pandas DataFrame. |
| `statistics` | One row per metric, using unique off-diagonal pairs. |
| `representativeness` | Mean scores by metric and dimension, overall score, and row dispersion. |
| `ranking` | Overall ranking: `rank`, `file`, and `score`. |
| `rankings_by_metric` | One ranking DataFrame per metric. |
| `most_representative` / `most_distinct` | Dictionaries containing `file`, `score`, and `rank`. |
| `medoid(metric)` | The file with highest mean similarity for that metric. |
| `composite(weights="equal")` | A separate weighted similarity DataFrame; it does not replace the normal ranking. |
| `with_metric(name, matrix)` | A new result containing a validated external matrix. |
| `export(directory, formats=("json", "csv"))` | Write JSON and/or CSV files. |
| `to_excel(path)` | Write one workbook. |

Custom composite weights must cover exactly the selected metrics, be finite
and nonnegative, and have a positive sum. External matrices must have unique
matching labels, finite real entries in [0, 1], symmetry, and diagonal 1.
Boolean and string entries are rejected. Names and dimensions must not collide
with reserved result columns. Derived tables are recalculated from the current
matrices; augmented results copy their matrices and metadata.

## Comparing groups

```text
compare_groups(group_a, group_b=None, *, metrics="all",
               group_names=("group_a", "group_b"), permutations=10_000,
               random_state=42, include_ast=False, alpha=0.05,
               progress=False, cache_dir=None, ast_timeout=120.0,
               max_ted_cells=2_000_000)
```

Pass two inputs (paths or lists of files) for a `GroupComparisonResult`, or a
mapping from group name to input for a `MultiGroupComparisonResult`. The mapping
form requires at least two groups. Each group needs at least two files; groups
must not share files, including through hardlinks. `progress` accepts a boolean
or a callback receiving messages.

The two-group result exposes `summary`, `overview`, `interpretation`,
`by_metric`, `by_dimension`, `within_groups`, `between_groups`, and `p_values`.
The mapping result exposes `global_test`, `pairwise`, `within_groups`,
`between_groups`, `overview`, `pairwise_overview`, and `interpretation`.
Both support `print_report()`, `export(directory)`, and `to_excel(path)`.

Group names must be distinct, nonempty strings. They are labels, not statistic
keys. For two groups, `within_group_columns` returns the two summary columns
in group order and is also recorded in metadata. Usually these are
`within_<name>`. If a label would collide with a statistic or another label,
`__group` is appended until the column is unique. Thus a group named
`difference` remains valid without replacing the `within_difference` contrast.
Use this property when reading arbitrary user-supplied group names.

Tests permute file-level group labels with fixed group sizes and use the
Monte Carlo correction `(extremes + 1)/(permutations + 1)`. Two-group
homogeneity and separation tests are two-sided. Global multigroup homogeneity
uses the upper tail of dispersion among within-group means. Holm correction
is applied separately by hypothesis family; pairwise correction includes all
reported pairs and measures. Group weighting is equal. These tests assume
exchangeable, independent files; repeated or paired observations need another
permutation design. See [examples/groups.py](../examples/groups.py).

## JavaScript integration and errors

`include_ast=True` adds structural JavaScript/TypeScript analysis only when
Python AST analysis is not already present. The installed `codevariability-js`
command must be on PATH. No sibling directory or another checkout is searched.
`ast_timeout` must be a positive finite number of seconds; timeout or adapter
failure raises `AnalysisError`. `cache_dir` is passed to the optional adapter.

Keep inputs unchanged throughout group analysis, including progress callbacks.
The optional adapter's raw input hashes must match those used by the base
analysis; a changed snapshot raises `AnalysisError` rather than combining
results from different file contents.

`load_matrix_json(path)` returns `(metric_name, pandas.DataFrame)` for the
single-metric schema in [FORMATS.md](FORMATS.md). Attach it with `with_metric()`.

Expected invalid input, incompatible metric, parsing, resource-budget, or
serialization failures raise `AnalysisError`. Filesystem failures may also
raise `OSError`. The CLI reports expected failures with a nonzero exit status.
