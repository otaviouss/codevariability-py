# Changelog

## 0.2.0 — 2026-10-03

- Correct empty Python AST input scores, including comment-only source and
  empty Python Markdown fences. Advance Python AST normalization to v3 while
  preserving the v2 TED formula and nonempty-tree behavior.

- Separate group means from statistic keys; expose `within_group_columns` for
  collision-safe access and preserve legitimate group labels.
- Replace the generic quoted-token fallback with linear scanning; keep token
  output semantics and existing normalization IDs.
- Reject optional JavaScript results when input hashes differ from the base
  analysis. Correct source typing and add permanent adversarial/property tests.
- Require scikit-learn >=1.5.0 and Pygments >=2.20.0 at runtime, setuptools
  >=83.0.0 for builds, and pytest >=9.0.3 for development to exclude known
  affected upstream versions. Add a source type-check gate.

- Prepare the independent `codevariability-py` distribution with a `src` layout,
  standalone tests, public examples, and explicit package contents.
- Preserve the Python API and metric definitions from the historical 0.1.2
  source. Keep `import codevariability` and the `codevariability` command.
- Resolve the optional JavaScript adapter only through its installed command;
  remove discovery of files outside the Python project.
- Document installation, outputs, resource limits, and migration from the
  historical distribution. This is the first release from the independent
  repository.
