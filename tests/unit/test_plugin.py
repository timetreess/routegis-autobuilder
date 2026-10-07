from __future__ import annotations

import pytest

from plugin import MINIMUM_PYTHON_VERSION, _assert_supported_python


def test_plugin_rejects_python_below_supported_floor() -> None:
    with pytest.raises(RuntimeError, match="requires Python 3.9 or later"):
        _assert_supported_python((3, 8))


def test_plugin_python_floor_matches_packaging_contract() -> None:
    assert MINIMUM_PYTHON_VERSION == (3, 9)
