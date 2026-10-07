from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from shapely.geometry.base import BaseGeometry


class PipelineState(str, Enum):
    NEW = "NEW"
    CAD_LOADED = "CAD_LOADED"
    ROUTE_SELECTED = "ROUTE_SELECTED"
    CRS_CONFIRMED = "CRS_CONFIRMED"
    AOI_CREATED = "AOI_CREATED"
    DOWNLOADING = "DOWNLOADING"
    VALIDATING = "VALIDATING"
    EXPORTING = "EXPORTING"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_WARNINGS = "COMPLETED_WITH_WARNINGS"
    FAILED = "FAILED"


@dataclass(frozen=True)
class ProjectConfig:
    project_name: str
    source_drawing: Path
    source_layers: tuple[str, ...]
    source_crs: str
    output_crs: str
    buffer_distance: float
    selected_layers: tuple[str, ...]
    output_directory: Path
    export_shapefile: bool = True
    snap_tolerance: float = 0.1
    chord_tolerance: float = 0.1
    dwg_converter: Path | None = None
    project_type: str = "linear"


@dataclass(frozen=True)
class LayerDefinition:
    id: str
    name: str
    provider: str
    service_type: str
    endpoint: str
    data_id: str
    geometry_type: str
    category: str
    request_crs: str
    publisher: str
    dataset_id: str
    license_name: str
    license_url: str
    response_crs: str | None = None
    aliases: tuple[str, ...] = ()


@dataclass
class SpatialFeature:
    geometry: BaseGeometry
    properties: dict[str, Any] = field(default_factory=dict)
    feature_id: str | None = None


@dataclass
class SpatialLayer:
    name: str
    display_name: str
    crs: str
    geometry_type: str
    category: str
    features: list[SpatialFeature] = field(default_factory=list)
    source: str = "local"

    @property
    def bounds(self) -> tuple[float, float, float, float] | None:
        non_empty = [feature.geometry for feature in self.features if not feature.geometry.is_empty]
        if not non_empty:
            return None
        return (
            min(geometry.bounds[0] for geometry in non_empty),
            min(geometry.bounds[1] for geometry in non_empty),
            max(geometry.bounds[2] for geometry in non_empty),
            max(geometry.bounds[3] for geometry in non_empty),
        )


@dataclass
class RouteGeometry:
    original: SpatialLayer
    transformed: SpatialLayer
    source_layers: tuple[str, ...]
    source_crs: str
    output_crs: str

    @property
    def length(self) -> float:
        return sum(feature.geometry.length for feature in self.transformed.features)

    @property
    def bbox(self) -> tuple[float, float, float, float] | None:
        return self.transformed.bounds


@dataclass
class DownloadResult:
    layer_id: str
    status: str
    feature_count: int = 0
    layer: SpatialLayer | None = None
    output: str | None = None
    error: str | None = None
    initial_tile_count: int = 0
    request_count: int = 0
    successful_tile_count: int = 0
    failed_tile_count: int = 0
    failed_tile_bbox: tuple[float, float, float, float] | None = None


@dataclass
class ValidationIssue:
    severity: str
    code: str
    message: str
    layer: str | None = None


@dataclass
class PipelineResult:
    state: PipelineState
    project_directory: Path
    project_file: Path | None = None
    geopackage: Path | None = None
    report_html: Path | None = None
    manifest: Path | None = None
    downloads: list[DownloadResult] = field(default_factory=list)
    issues: list[ValidationIssue] = field(default_factory=list)
