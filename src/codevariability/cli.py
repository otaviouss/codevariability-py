"""Command-line entry point."""

from __future__ import annotations

import argparse

from . import __version__
from .analysis import analyze
from .exceptions import AnalysisError
from .metrics import METRICS


def _cell_limit(value: str) -> int | None:
    if value == "none":
        return None
    try:
        limit = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use um inteiro positivo ou 'none'.") from exc
    if limit < 1:
        raise argparse.ArgumentTypeError("Use um inteiro positivo ou 'none'.")
    return limit


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="codevariability",
        description="Analisa similaridade e variabilidade de código.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    command = subcommands.add_parser("analyze", help="Analisa uma pasta de código.")
    command.add_argument("directory")
    command.add_argument(
        "--metrics", nargs="+", choices=[*METRICS, "all"], default=["all"]
    )
    command.add_argument(
        "--extensions",
        nargs="+",
        help="Filtra extensões no diretório, por exemplo: py java rs.",
    )
    command.add_argument("--output", help="Diretório para JSON/CSV ou arquivo .xlsx.")
    command.add_argument(
        "--max-ted-cells",
        type=_cell_limit,
        default=2_000_000,
        help="Limite de células por par AST (padrão: 2000000); 'none' remove o limite.",
    )
    args = parser.parse_args()
    if "all" in args.metrics and args.metrics != ["all"]:
        parser.error("--metrics all não pode ser combinado com métricas individuais")
    metrics = "all" if args.metrics == ["all"] else args.metrics
    try:
        result = analyze(
            args.directory,
            metrics=metrics,
            extensions=args.extensions,
            max_ted_cells=args.max_ted_cells,
        )
        if args.output:
            result.to_excel(args.output) if args.output.lower().endswith(
                ".xlsx"
            ) else result.export(args.output)
    except (AnalysisError, OSError) as exc:
        parser.exit(1, f"codevariability: {exc}\n")
    for metric, stats in result.statistics.iterrows():
        print(
            f"{metric}: similaridade média={stats['mean_similarity']!s}; variabilidade média={stats['mean_variability']!s}"
        )


if __name__ == "__main__":
    main()
