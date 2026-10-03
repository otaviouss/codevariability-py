# CodeVariability for Python

CodeVariability compares UTF-8 source-code files and returns similarity
matrices, descriptive statistics, representative rankings, and comparisons
between independent groups. It helps you identify common structures and
unusual variants in a collection without executing the submitted code.

Version **0.2.0** is the first release of this independent repository. The API is alpha. The distribution name is `codevariability-py`;
the import name and command are `codevariability`.

## Installation

Python 3.10 or later is required. Install from PyPI:

```bash
python -m pip install codevariability-py==0.2.0
```

For development, install a checkout with `python -m pip install .`. Do not install the historical `codevariability`
distribution in the same environment: both distributions provide the same
Python import and command. Use a new virtual environment when migrating.

## Quick start

This example creates its own inputs and works with an installed package:

```python
from pathlib import Path
from tempfile import TemporaryDirectory
from codevariability import analyze

with TemporaryDirectory() as directory:
    folder = Path(directory)
    (folder / "a.py").write_text("def total(a, b):\n    return a + b\n", encoding="utf-8")
    (folder / "b.py").write_text("def sum_values(x, y):\n    return x + y\n", encoding="utf-8")
    result = analyze(folder, metrics="all")
    print(result.statistics)
    print(result.ranking)
    print(result.most_representative)
```

The runnable [example](https://github.com/otaviouss/codevariability-py/blob/main/examples/basic.py) also exports JSON, CSV, and Excel.
For the bundled input files, the CLI is:

```bash
codevariability analyze examples/inputs --metrics all --output analysis-output
codevariability analyze examples/inputs --metrics cosine jaccard --output analysis-output/report.xlsx
```

## Inputs and metrics

Pass a directory or a list of file paths to `analyze()`. Directory scans are
not recursive. File basenames must be unique. Files are decoded as UTF-8,
including an optional BOM. Use `extensions="py"` or a list of extensions to
filter a directory.

| Metric | What it compares |
| --- | --- |
| `cosine` | Word frequencies in the full document. |
| `jaccard` | Sets of words in the full document. |
| `lcs` | The longest common subsequence of code tokens. |
| `levenshtein` | Token sequences using unit edit costs. |
| `ast_tree_edit_similarity` | Ordered, normalized Python syntax trees. |

`metrics="all"` includes all applicable metrics. Python AST analysis requires
Python inputs; explicit selection on incompatible inputs raises `AnalysisError`.
Source files are read in full. For Markdown, token and AST metrics use the
identified fenced code blocks; textual metrics include the full document.

## Results and export

`AnalysisResult` exposes `matrices`, `statistics`, `representativeness`,
`ranking`, `rankings_by_metric`, `most_representative`, and `most_distinct`.
Similarities range from 0 to 1. Statistics use each unique pair once. Rankings
average within each available dimension and then give dimensions equal weight.

Use `result.export(directory, formats=("json", "csv"))` for machine-readable
results and tables, or `result.to_excel(path)` for a workbook. Outputs include
metric identifiers, normalization and runtime versions, and input hashes.
Spreadsheet exports escape formula-like labels; JSON retains the original
labels. See [input/output details](https://github.com/otaviouss/codevariability-py/blob/main/docs/FORMATS.md).

`compare_groups()` supports two groups or a mapping of two or more groups,
file-label permutation tests, and Holm-adjusted p-values. Each group needs at
least two files and groups must not share physical files. A complete example
is in [examples/groups.py](https://github.com/otaviouss/codevariability-py/blob/main/examples/groups.py).

## Optional JavaScript integration

The Python library works without Node.js. To request structural JavaScript or
TypeScript analysis through `compare_groups(..., include_ast=True)`, install
the independent [codevariability-js](https://github.com/otaviouss/codevariability-js)
package and make its command available on `PATH`. Alternatively, import a
single-metric JSON with `load_matrix_json()` and attach it with `with_metric()`.
The Python build and normal test suite require no JavaScript checkout.

## Limits and errors

Scores describe text, tokens, or syntax; they do not establish functional
equivalence, correctness, authorship, or copied-code percentages. AST
normalization removes concrete names and literal values. Different sources
can therefore have structural similarity 1.

Exact tree comparison can be expensive. The default `max_ted_cells=2_000_000`
limits tables for each non-identical AST pair; exceeding it raises an error,
without approximation. `None` removes the limit. It does not limit source
file size, the full pairwise matrix, or total CPU time. Use trusted output
directories and apply application-level resource limits for untrusted inputs.

Expected input and serialization errors raise `AnalysisError`. JavaScript
integration also supports `ast_timeout=120` seconds. See the
[API](https://github.com/otaviouss/codevariability-py/blob/main/docs/API.md) and [metric definitions](https://github.com/otaviouss/codevariability-py/blob/main/docs/METRICS.md) for details.

## Contributing and license

See [CONTRIBUTING.md](https://github.com/otaviouss/codevariability-py/blob/main/CONTRIBUTING.md) for development and
[PUBLICATION_CHECKLIST.md](https://github.com/otaviouss/codevariability-py/blob/main/PUBLICATION_CHECKLIST.md) for release preparation.
The project uses the [MIT license](https://github.com/otaviouss/codevariability-py/blob/main/LICENSE), copyright 2026 Otávio Gomes.

## Compatibility

Arbitrary distinct nonempty group names are
accepted; use `result.within_group_columns` to locate collision-safe within-group
means. Keep files unchanged while an analysis runs; optional JavaScript results
with different input hashes raise `AnalysisError`.

Runtime minimums are scikit-learn 1.5.0 and Pygments 2.20.0; build minimum is
setuptools 83.0.0 and development tests require pytest 9.0.3. These scopes are
separate. See [API](docs/API.md) and [metric limits](docs/METRICS.md).

Python AST normalization now uses v3 to identify corrected empty-program
behavior. The TED formula ID remains v2; use compatible normalization IDs when
comparing results across versions.
