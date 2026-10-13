from __future__ import annotations

import shutil
import zipfile
from collections.abc import Mapping
from pathlib import Path

from ..config import resource_root
from ..models import SpatialLayer
from .headless_project import _headless_qgs
from .native_project import _native_project
from .project_schema import (
    GROUPS,
    STYLE_NAMES,
    VWORLD_HYBRID_GROUP_NAME,
    VWORLD_HYBRID_LAYERS,
    VWORLD_STREET_NAME,
    VWORLD_STREET_URI,
    _layer_id,
    _project_relative_path,
)
from .renderers import _renderer

__all__ = [
    "GROUPS",
    "STYLE_NAMES",
    "VWORLD_HYBRID_GROUP_NAME",
    "VWORLD_HYBRID_LAYERS",
    "VWORLD_STREET_NAME",
    "VWORLD_STREET_URI",
    "write_qgis_project",
]


def _copy_styles(project_directory: Path) -> None:
    style_directory = project_directory / "style"
    style_directory.mkdir(parents=True, exist_ok=True)
    for style in (resource_root() / "styles").glob("*.qml"):
        shutil.copy2(style, style_directory / style.name)


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
    use_native: bool = True,
) -> Path:
    path = Path(path).with_suffix(".qgz")
    path.parent.mkdir(parents=True, exist_ok=True)
    _project_relative_path(path.parent, geopackage, label="GeoPackage")
    _project_relative_path(path.parent, source_drawing, label="원본 도면")
    # Worker threads use the XML writer, avoiding QGIS QObject creation/access.
    native = use_native and _native_project(
        path,
        project_name,
        geopackage,
        layers,
        source_drawing,
        source_crs,
        layer_metadata,
    )
    if not native:
        qgs_name = f"{path.stem}.qgs"
        qgs = _headless_qgs(
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
                qgs_name,
                date_time=(1980, 1, 1, 0, 0, 0),
            )
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, qgs)
    _copy_styles(path.parent)
    return path
