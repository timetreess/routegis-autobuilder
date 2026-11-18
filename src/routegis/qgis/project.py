from __future__ import annotations

import zipfile
from collections.abc import Mapping
from pathlib import Path

from ..models import SpatialLayer
from .headless_project import headless_qgs
from .project_schema import (
    GROUPS,
    VWORLD_HYBRID_GROUP_NAME,
    VWORLD_HYBRID_LAYERS,
    VWORLD_STREET_NAME,
    VWORLD_STREET_URI,
    project_relative_path,
)
from .qml import write_qml_files

__all__ = [
    "GROUPS",
    "VWORLD_HYBRID_GROUP_NAME",
    "VWORLD_HYBRID_LAYERS",
    "VWORLD_STREET_NAME",
    "VWORLD_STREET_URI",
    "write_qgis_project",
]


def write_qgis_project(
    path: Path,
    project_name: str,
    geopackage: Path,
    layers: list[SpatialLayer],
    source_drawing: Path,
    source_crs: str,
    *,
    template_version: str = "3.40.0",
    layer_metadata: Mapping[str, Mapping[str, str]] | None = None,
) -> Path:
    path = Path(path).with_suffix(".qgz")
    path.parent.mkdir(parents=True, exist_ok=True)
    project_relative_path(path.parent, geopackage, label="GeoPackage")
    project_relative_path(path.parent, source_drawing, label="원본 도면")
    # Write project XML directly so background builds never create QGIS objects.
    qgs = headless_qgs(
        project_name,
        path.parent,
        geopackage,
        layers,
        source_drawing,
        source_crs,
        template_version,
        layer_metadata,
    )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo(
            f"{path.stem}.qgs",
            date_time=(1980, 1, 1, 0, 0, 0),
        )
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, qgs)
    write_qml_files(path.parent / "style")
    return path
