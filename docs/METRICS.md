# Metric definitions and limits

Text normalization applies Unicode NFC, case folding, and Unicode word
extraction to the entire document. `cosine` uses raw word-count vectors,
not TF-IDF; an empty vector has zero similarity to other files, even another
empty document. `jaccard` uses word sets, returning 1 for two empty sets.
All result matrices explicitly have diagonal 1.

For code-token metrics, `.py` and other source files are read in full.
Markdown uses identified fenced blocks; backtick and tilde fences are supported,
with the first word of the opening information string naming the language.
Unclosed fences raise an error. Markdown without code blocks has an empty code
sequence. Pygments handles known languages; unknown languages use a generic
deterministic tokenizer. Comments and whitespace are excluded.

`lcs` divides longest common subsequence length by the longer token sequence.
`levenshtein` uses unit insertion, deletion, and substitution costs and returns
`1 - distance / max_length`. Two empty sequences have similarity 1; an empty
and a nonempty sequence have similarity 0.

Python AST normalization preserves ordered syntax nodes, operators, literal
categories, and structural fields. Concrete identifiers, literal values,
comments, formatting, and locations are removed. Identified Python Markdown
fragments appear in order under a synthetic root. Empty or comment-only
fragments are ignored. Python grammar depends on the running interpreter.

`ast_tree_edit_similarity` uses exact ordered Zhang–Shasha tree edit distance
with unit insertion, deletion, and relabeling costs. For nonempty normalized
trees with `n` and `m` nodes:

```text
similarity = 1 - tree_edit_distance / (n + m - 1)
```

The denominator is a valid upper bound: remove non-root nodes, relabel the
root if necessary, and insert the target's non-root nodes. Empty/empty has
similarity 1; empty/nonempty has similarity 0. The score is symmetric and in
[0, 1], but does not establish semantic or functional equivalence.

Exact TED uses O(nm) table memory and shape-dependent computation. Its default
budget is 2,000,000 cells `(n+1)*(m+1)` for a non-identical pair. Equal trees
are checked first without those tables. `max_ted_cells=None` removes the
budget. Parsing, full matrices, file sizes, and permutations have separate
resource costs that the caller must manage.

Rankings first average metrics within each dimension (`textual`,
`syntactic_token_sequence`, `structural_ast`), then average available dimensions
equally. If an external AST node-frequency baseline and TED are both present,
only TED enters the structural dimension. `overall_dispersion` is the sample
standard deviation of a file's off-diagonal values in the equally weighted
dimension-average matrix; it is 0 when fewer than two values exist.

Internal IDs and normalization versions are recorded in metadata. Python AST
uses `ast_tree_edit_similarity_v2` and `python_normalized_ast_tree_v2`.
Compare results only when metric IDs, normalization, runtime/parser versions,
and selected dimensions are compatible.
