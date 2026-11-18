"""Build the sample project offline with fixture data for release validation.

How to run (after installing the package): python scripts/fixture_build.py <output-directory>
"""

from __future__ import annotations

import sys
from pathlib import Path

from routegis.app.project_service import ProjectService
from routegis.config import load_profile
from routegis.models import ProjectConfig
from routegis.providers.fixture import FixtureProvider

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = PROJECT_ROOT / "tests" / "fixtures"


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: fixture_build.py <output-directory>", file=sys.stderr)
        return 2
    result = ProjectService().build(
        ProjectConfig(
            project_name="release-smoke",
            source_drawing=FIXTURES / "sample_route.dxf",
            source_crs="EPSG:5186",
            output_crs="EPSG:5186",
            buffer_distance=1000,
            selected_layers=load_profile(),
            output_directory=Path(argv[0]).resolve(),
        ),
        provider=FixtureProvider(FIXTURES / "spatial_mock.json"),
    )
    print(result.project_directory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
