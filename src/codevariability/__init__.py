"""Public API for multidimensional source-code similarity analysis."""

from .analysis import AnalysisResult, CodeDataset, analyze
from .exceptions import AnalysisError
from .group_comparison import (
    GroupComparisonResult,
    MultiGroupComparisonResult,
    compare_groups,
)
from .interop import load_matrix_json

__all__ = [
    "AnalysisError",
    "AnalysisResult",
    "CodeDataset",
    "GroupComparisonResult",
    "MultiGroupComparisonResult",
    "analyze",
    "compare_groups",
    "load_matrix_json",
]

__version__ = "0.2.0"
