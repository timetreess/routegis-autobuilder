"""Compatibility launcher for the RouteGIS AutoBuilder CLI.

The original single-file prototype has moved to the tested ``routegis`` package.
Run ``python qgis_automation.py --help`` or install the package and use ``routegis``.
"""

from pathlib import Path
import sys


SOURCE = Path(__file__).resolve().parent / "src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from routegis.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
