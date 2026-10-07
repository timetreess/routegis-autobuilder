from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import NamedTuple
from xml.etree import ElementTree as ET

from ..errors import ExportError
from ..gis.crs import crs_wkt
from ..gis.export import shapefile_export_path
from ..models import SpatialLayer


GROUPS = (
    "01_노선",
    "02_생태환경",
    "03_국가유산",
    "04_수자원",
    "05_전력시설",
    "06_행정구역",
    "07_토지이용",
    "08_시설물",
    "09_원본자료",
    "10_토지",
    "11_기본도",
)

STYLE_NAMES = {
    "ecology_nature": "ecology",
    "heritage_protection": "heritage",
    "river_centerline": "river_line",
    "river_area": "river_area",
    "power_line": "power_line",
    "power_support": "power_support",
}

QGIS_METADATA_FIELDS = (
    "provider",
    "publisher",
    "dataset_id",
    "attribution",
    "license_name",
    "license_url",
)

VWORLD_STREET_NAME = "VWorld Street"
VWORLD_STREET_URI = (
    "url=https://xdworld.vworld.kr/2d/Base/service/{z}/{x}/{y}.png"
    "&zmax=18&zmin=0&type=xyz&tilePixelRatio=1"
)
VWORLD_HYBRID_GROUP_NAME = "VWorld Hybrid"
VWORLD_HYBRID_LAYERS = (
    (
        "VWorld Hybrid Label",
        "url=https://xdworld.vworld.kr/2d/Hybrid/service/{z}/{x}/{y}.png"
        + "&zmax=18&zmin=0&type=xyz&tilePixelRatio=1",
    ),
    (
        "VWorld Hybrid",
        "url=https://xdworld.vworld.kr/2d/Satellite/service/{z}/{x}/{y}.jpeg"
        + "&zmax=18&zmin=0&type=xyz&tilePixelRatio=1",
    ),
)


class VectorSource(NamedTuple):
    absolute_uri: str
    relative_uri: str
    encoding: str


def _project_relative_path(
    project_directory: Path,
    path: Path,
    *,
    label: str,
) -> str:
    project_directory = Path(project_directory).resolve(strict=False)
    path = Path(path).resolve(strict=False)
    try:
        return path.relative_to(project_directory).as_posix()
    except ValueError as exc:
        raise ExportError(
            f"{label} 경로가 프로젝트 폴더를 벗어났습니다: {path}"
        ) from exc


def _group_for(layer: SpatialLayer) -> str:
    return {
        "route": "01_노선",
        "land": "10_토지",
        "cadastral": "10_토지",
        "ecology": "02_생태환경",
        "heritage": "03_국가유산",
        "water": "04_수자원",
        "power": "05_전력시설",
        "administrative": "06_행정구역",
        "landuse": "07_토지이용",
        "facility": "08_시설물",
        "source": "09_원본자료",
    }.get(layer.category, "08_시설물")


def _group_labels(layers: list[SpatialLayer]) -> dict[str, str]:
    feature_counts: dict[str, int] = {}
    for layer in layers:
        name = _group_for(layer)
        feature_counts[name] = feature_counts.get(name, 0) + len(layer.features)
    return {
        name: f"{name} (해당없음)" if feature_counts.get(name) == 0 else name
        for name in GROUPS
    }


def _style_name(layer: SpatialLayer) -> str:
    if layer.name in STYLE_NAMES:
        return STYLE_NAMES[layer.name]
    if layer.name == "route_buffer":
        return "buffer"
    if layer.category == "route":
        return "route"
    return layer.category


def _provider_properties(
    layer_name: str,
    layer_metadata: Mapping[str, Mapping[str, str]],
) -> dict[str, str]:
    metadata = layer_metadata.get(layer_name)
    if metadata is None:
        return {}
    return {f"routegis/{field}": metadata[field] for field in QGIS_METADATA_FIELDS}


def _layer_id(name: str) -> str:
    digest = hashlib.sha1(name.encode("utf-8"), usedforsecurity=False).hexdigest()
    return f"{name}_{digest[:12]}"


def _srs_element(parent: ET.Element, crs: str) -> None:
    spatial = ET.SubElement(parent, "spatialrefsys")
    ET.SubElement(spatial, "wkt").text = crs_wkt(crs, version="WKT1_GDAL")
    authority, code = crs.upper().split(":", 1)
    ET.SubElement(spatial, "proj4")
    ET.SubElement(spatial, "srsid").text = code
    ET.SubElement(spatial, "srid").text = code
    ET.SubElement(spatial, "authid").text = f"{authority}:{code}"
    ET.SubElement(spatial, "description").text = crs
    ET.SubElement(spatial, "projectionacronym")
    ET.SubElement(spatial, "ellipsoidacronym")
    ET.SubElement(spatial, "geographicflag").text = "false"


def _vector_source(
    project_directory: Path,
    geopackage: Path,
    layer: SpatialLayer,
) -> VectorSource:
    project_directory = Path(project_directory).resolve(strict=False)
    geopackage = Path(geopackage).resolve(strict=False)
    shapefile = shapefile_export_path(project_directory, layer)
    if shapefile.is_file():
        return VectorSource(
            absolute_uri=str(shapefile),
            relative_uri=shapefile.relative_to(project_directory).as_posix(),
            encoding="EUC-KR",
        )
    return VectorSource(
        absolute_uri=f"{geopackage}|layername={layer.name}",
        relative_uri=(
            f"{_project_relative_path(project_directory, geopackage, label='GeoPackage')}"
            f"|layername={layer.name}"
        ),
        encoding="UTF-8",
    )
