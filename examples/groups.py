"""Compare independent groups using only Python files."""

from pathlib import Path
from tempfile import TemporaryDirectory

from codevariability import compare_groups


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        groups = {}
        for name, sources in {
            "group_a": ["x = a + b", "y = c + d"],
            "group_b": ["class Item:\n    pass", "while ready:\n    work()"],
            "group_c": ["for item in items:\n    print(item)", "if active:\n    start()"],
        }.items():
            folder = root / name
            folder.mkdir()
            for index, source in enumerate(sources):
                (folder / f"{index}.py").write_text(source, encoding="utf-8")
            groups[name] = folder
        result = compare_groups(groups, metrics=["cosine", "jaccard"], permutations=99, random_state=42)
        result.print_report()
        result.export(root / "output")
        result.to_excel(root / "output/groups.xlsx")
        assert (root / "output/group_comparison.json").is_file()


if __name__ == "__main__":
    main()
