from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from pathlib import Path

from ..errors import ExportError
from ..models import SpatialLayer
from .gpkg import write_geopackage
from .shapefile import (
    SHAPEFILE_SUFFIXES,
    DbfEncodingError,
    validate_export_path_component,
    write_shapefile,
)


METADATA_FIELDS = (
    "id",
    "name",
    "provider",
    "data_id",
    "dataset_id",
    "publisher",
    "attribution",
    "license_name",
    "license_url",
)


def shapefile_export_path(project_directory: Path, layer: SpatialLayer) -> Path:
    category = "route" if layer.category == "route" else layer.category
    filename = "선형" if layer.name == "linework" else layer.name
    validate_export_path_component(category, label="Shapefile 분류")
    validate_export_path_component(filename, label="Shapefile 레이어 이름")
    shapefile_root = (Path(project_directory) / "shp").resolve(strict=False)
    path = (shapefile_root / category / f"{filename}.shp").resolve(strict=False)
    try:
        path.relative_to(shapefile_root)
    except ValueError as exc:
        raise ExportError(
            f"Shapefile 경로가 프로젝트 폴더를 벗어났습니다: {path}"
        ) from exc
    return path


def _write_geopackage_metadata(
    geopackage: Path,
    layer_metadata: Mapping[str, Mapping[str, str]],
    layer_names: set[str],
) -> None:
    unexpected = sorted(set(layer_metadata) - layer_names)
    if unexpected:
        raise ExportError(
            "내보내지 않은 레이어의 메타데이터가 포함되었습니다: " + ", ".join(unexpected)
        )
    rows: list[tuple[str, ...]] = []
    for layer_id in sorted(layer_metadata):
        metadata = layer_metadata[layer_id]
        if set(metadata) != set(METADATA_FIELDS) or metadata.get("id") != layer_id:
            raise ExportError(f"레이어 출처 메타데이터 형식이 올바르지 않습니다: {layer_id}")
        values = tuple(metadata[field] for field in METADATA_FIELDS)
        if any(not isinstance(value, str) or not value for value in values):
            raise ExportError(f"레이어 출처 메타데이터 값이 올바르지 않습니다: {layer_id}")
        rows.append(values)

    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(geopackage)
        connection.execute(
            """
            CREATE TABLE routegis_layer_metadata (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              provider TEXT NOT NULL,
              data_id TEXT NOT NULL,
              dataset_id TEXT NOT NULL,
              publisher TEXT NOT NULL,
              attribution TEXT NOT NULL,
              license_name TEXT NOT NULL,
              license_url TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO gpkg_contents (
              table_name, data_type, identifier, description, last_change,
              min_x, min_y, max_x, max_y, srs_id
            ) VALUES (?, 'attributes', ?, ?, ?, NULL, NULL, NULL, NULL, NULL)
            """,
            (
                "routegis_layer_metadata",
                "RouteGIS layer metadata",
                "RouteGIS provider attribution and license metadata",
                "1980-01-01T00:00:00.000Z",
            ),
        )
        connection.executemany(
            "INSERT INTO routegis_layer_metadata VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise ExportError("GeoPackage 메타데이터 무결성 검사에 실패했습니다.")
        connection.commit()
    except sqlite3.Error as exc:
        raise ExportError(f"GeoPackage 출처 메타데이터를 저장하지 못했습니다: {exc}") from exc
    finally:
        if connection is not None:
            connection.close()


def export_layers(
    project_directory: Path,
    layers: list[SpatialLayer],
    *,
    export_shapefiles: bool = True,
    layer_metadata: Mapping[str, Mapping[str, str]] | None = None,
) -> tuple[Path, dict[str, dict[str, str]]]:
    shapefile_root = project_directory / "shp"
    shapefile_exports: list[tuple[SpatialLayer, Path]] = []
    if export_shapefiles:
        export_names_by_path: dict[str, str] = {}
        for layer in layers:
            path = shapefile_export_path(project_directory, layer)
            path_key = str(path).casefold()
            existing_name = export_names_by_path.get(path_key)
            if existing_name is not None:
                raise ExportError(
                    "여러 레이어를 동일한 Shapefile 경로에 저장할 수 없습니다: "
                    f"{existing_name}, {layer.name} -> {path}"
                )
            export_names_by_path[path_key] = layer.name
            shapefile_exports.append((layer, path))

    geopackage = write_geopackage(project_directory / "data" / "project.gpkg", layers)
    metadata = layer_metadata or {}
    if layer_metadata is not None:
        _write_geopackage_metadata(geopackage, metadata, {layer.name for layer in layers})
    mappings: dict[str, dict[str, str]] = {}
    if export_shapefiles:
        for layer, path in shapefile_exports:
            try:
                mappings[layer.name] = write_shapefile(
                    path,
                    layer,
                    root_directory=shapefile_root,
                    metadata=metadata.get(layer.name),
                )
            except DbfEncodingError:
                for suffix in SHAPEFILE_SUFFIXES:
                    path.with_suffix(suffix).unlink(missing_ok=True)
                continue
        mapping_path = shapefile_root / "field_mapping.json"
        mapping_path.parent.mkdir(parents=True, exist_ok=True)
        mapping_path.write_text(
            json.dumps(mappings, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    return geopackage, mappings
