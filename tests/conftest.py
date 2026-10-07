from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def fixture_directory() -> Path:
    return Path(__file__).parent / "fixtures"
