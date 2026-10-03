"""Run with: python examples/basic.py (after installing the package)."""

from pathlib import Path
from tempfile import TemporaryDirectory

from codevariability import analyze


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        inputs = root / "inputs"
        inputs.mkdir()
        (inputs / "a.py").write_text("def total(a, b):\n    return a + b\n", encoding="utf-8")
        (inputs / "b.py").write_text("def sum_values(x, y):\n    return x + y\n", encoding="utf-8")
        result = analyze(inputs, metrics="all")
        result.export(root / "output")
        result.to_excel(root / "output/report.xlsx")
        print(result.statistics)
        print(result.ranking)
        assert (root / "output/analysis.json").is_file()
        assert (root / "output/report.xlsx").is_file()


if __name__ == "__main__":
    main()
