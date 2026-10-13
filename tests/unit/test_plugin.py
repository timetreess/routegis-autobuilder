from __future__ import annotations

import pytest

from plugin import _assert_supported_python


def test_plugin_rejects_python_below_supported_floor() -> None:
    with pytest.raises(RuntimeError, match="requires Python 3.9 or later"):
        _assert_supported_python((3, 8))
