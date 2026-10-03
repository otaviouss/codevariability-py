"""Load and attach a single-metric interchange matrix."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from codevariability import analyze, load_matrix_json


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        for name, source in {"a.py": "x=1", "b.py": "y=2"}.items():
            (root / name).write_text(source, encoding="utf-8")
        result = analyze([root / "a.py", root / "b.py"], metrics="cosine")
        matrix_file = root / "matrix.json"
        matrix_file.write_text(json.dumps({
            "schema_version": "codevariability.matrix.v1",
            "metric": "custom_similarity",
            "files": result.files,
            "matrix": [[1, 0.75], [0.75, 1]],
        }), encoding="utf-8")
        metric, matrix = load_matrix_json(matrix_file)
        augmented = result.with_metric(metric, matrix)
        print(augmented.rankings_by_metric[metric])
        assert augmented.matrices[metric].iat[0, 1] == 0.75


if __name__ == "__main__":
    main()
