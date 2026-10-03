# Contributing

Use Python 3.10 or later. Create a virtual environment, install the development
extra, then run the independent Python checks:

```bash
python -m pip install -e '.[dev]'
python -m ruff check src tests examples
python -m pytest
python -m build
```

Node.js is optional. The JavaScript integration test skips when the separate
adapter command is unavailable; all Python tests and builds run independently.

For bug reports, include versions, selected metric names, and the smallest
reproducible inputs that can be shared publicly. Keep credentials and private
source code out of reports. Pull requests should describe observable behavior,
include relevant regression tests, and update the documentation.

Preserve metric identifiers when definitions remain equivalent. Changes to
normalization, parsing, or score definitions need a new identifier and a
migration note. See [release instructions](docs/RELEASING.md).
