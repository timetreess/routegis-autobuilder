from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from shapely.geometry import LineString

from ..cad.reader import DxfCoordinateReport
from ..models import ValidationIssue


@dataclass(frozen=True)
class DrawingAnalysis:
    """Everything one DXF analysis produced; the caller owns it."""

    candidates: list[dict[str, Any]]
    coordinate_report: DxfCoordinateReport
    lines_by_layer: dict[str, list[LineString]] = field(default_factory=dict)
    warnings: tuple[ValidationIssue, ...] = ()
