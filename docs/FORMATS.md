# Inputs and outputs

Source files use UTF-8, optionally with BOM. Directory discovery is nonrecursive
and uses natural filename ordering with deterministic ties. All regular files
are considered unless an extension filter is supplied. Explicit file lists
must have unique basenames. No submitted program is executed.

## Analysis files

`export()` writes `analysis.json`, `statistics.csv`, `representativeness.csv`,
`ranking.csv`, `<metric>_similarity_matrix.csv`, and `<metric>_ranking.csv`.
Select only JSON or CSV with the `formats` parameter. JSON contains file names,
metrics, metadata, matrices, statistics, and rankings. Non-simple external
metric names receive a safe filename containing a hash suffix; the original
name is retained inside the result and JSON.

The workbook contains Summary, Metadata, Representativeness, Ranking,
Statistics, and one sheet per metric. Group exports have separate group
statistics files and an overview workbook.

File and group labels resembling formulas are escaped in CSV and Excel,
including row and column labels. Escaped CSV labels have a leading tab inside
quoted fields; workbook labels use a leading apostrophe. JSON keeps exact
labels and is the preferred interchange format when labels matter.

Individual output files are replaced atomically. Existing destination symlinks
are rejected. Multi-file export is not a transaction over the entire directory.
Output/cache directories should be trusted; the library does not confine all
filesystem operations to a sandbox root. Input hashes identify file bytes and
are included with runtime, metric, normalization, and aggregation metadata.

## External matrix JSON

The supported schema is `codevariability.matrix.v1`. It requires `metric`,
unique nonempty `files`, and a square real-number `matrix`. Optional `metric_id`
and `metadata` identify the adapter, normalization, and metric dimension.
The matrix must be symmetric, have diagonal 1, and contain finite values in
[0, 1]. Duplicate JSON keys, booleans, implicit numeric strings, and nonfinite
numbers are rejected. Multi-metric `codevariability.analysis.v1` output from
JavaScript is not accepted by `load_matrix_json()`; export one metric instead.

A self-contained loading and attachment example is in
[examples/interop.py](../examples/interop.py).
