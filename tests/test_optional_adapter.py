from unittest.mock import patch

import pytest

from codevariability import AnalysisError
from codevariability.group_comparison import _javascript_adapter_command


def test_missing_optional_adapter_has_an_explicit_error():
    with patch("codevariability.group_comparison.shutil.which", return_value=None):
        with pytest.raises(AnalysisError, match="codevariability-js instalado"):
            _javascript_adapter_command()
