from __future__ import annotations

import json
import sqlite3
import struct
from pathlib import Path
from typing import Any

from shapely import to_wkb

from ..errors import ExportError
from ..models import SpatialLayer
from .crs import crs_wkt


LAST_CHANGE = "1980-01-01T00:00:00.000Z"


def _identifier(name: str) -> str:
    if not name or "\x00" in name:
        raise ExportError(f"GeoPackage 레이어/필드 이름이 올바르지 않습니다: {name}")
    return f'"{name.replace(chr(34), chr(34) * 2)}"'


def _epsg_code(crs: str) -> int:
    try:
        authority, code = crs.upper().split(":", 1)
        if authority != "EPSG":
            raise ValueError
        return int(code)
    except ValueError as exc:
        raise ExportError(f"GeoPackage에는 EPSG 좌표계가 필요합니다: {crs}") from exc


def _property_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bytes)):
        return value
    if isinstance(value, bool):
        return int(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _field_type(values: list[Any]) -> str:
    non_null = [value for value in values if value is not None]
    if not non_null:
        return "TEXT"
    if all(isinstance(value, (bool, int)) for value in non_null):
        return "INTEGER"
    if all(isinstance(value, (bool, int, float)) for value in non_null):
        return "REAL"
    if all(isinstance(value, bytes) for value in non_null):
        return "BLOB"
    return "TEXT"


def _geometry_blob(layer: SpatialLayer, feature_index: int) -> bytes:
    geometry = layer.features[feature_index].geometry
    if geometry.is_empty or not geometry.is_valid:
        raise ExportError(f"유효하지 않은 Geometry는 GeoPackage에 저장할 수 없습니다: {layer.name}")
    min_x, min_y, max_x, max_y = geometry.bounds
    header = b"GP" + bytes((0, 3)) + struct.pack("<i4d", _epsg_code(layer.crs), min_x, max_x, min_y, max_y)
    return header + to_wkb(geometry, hex=False, output_dimension=2, byte_order=1, include_srid=False, flavor="iso")


def _initialize(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA application_id = 1196444487")
    connection.execute("PRAGMA user_version = 10300")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(
        """
        CREATE TABLE gpkg_spatial_ref_sys (
          srs_name TEXT NOT NULL,
          srs_id INTEGER NOT NULL PRIMARY KEY,
          organization TEXT NOT NULL,
          organization_coordsys_id INTEGER NOT NULL,
          definition TEXT NOT NULL,
          description TEXT
        );
        CREATE TABLE gpkg_contents (
          table_name TEXT NOT NULL PRIMARY KEY,
          data_type TEXT NOT NULL,
          identifier TEXT UNIQUE,
          description TEXT DEFAULT '',
          last_change DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
          min_x DOUBLE,
          min_y DOUBLE,
          max_x DOUBLE,
          max_y DOUBLE,
          srs_id INTEGER,
          CONSTRAINT fk_gc_r_srs_id FOREIGN KEY (srs_id) REFERENCES gpkg_spatial_ref_sys(srs_id)
        );
        CREATE TABLE gpkg_geometry_columns (
          table_name TEXT NOT NULL,
          column_name TEXT NOT NULL,
          geometry_type_name TEXT NOT NULL,
          srs_id INTEGER NOT NULL,
          z TINYINT NOT NULL,
          m TINYINT NOT NULL,
          CONSTRAINT pk_geom_cols PRIMARY KEY (table_name, column_name),
          CONSTRAINT uk_gc_table_name UNIQUE (table_name),
          CONSTRAINT fk_gc_tn FOREIGN KEY (table_name) REFERENCES gpkg_contents(table_name),
          CONSTRAINT fk_gc_srs FOREIGN KEY (srs_id) REFERENCES gpkg_spatial_ref_sys(srs_id)
        );
        """
    )
    connection.executemany(
        "INSERT INTO gpkg_spatial_ref_sys VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("Undefined Cartesian SRS", -1, "NONE", -1, "undefined", "undefined Cartesian coordinate reference system"),
            ("Undefined Geographic SRS", 0, "NONE", 0, "undefined", "undefined geographic coordinate reference system"),
            (
                "WGS 84 geodetic",
                4326,
                "EPSG",
                4326,
                crs_wkt("EPSG:4326", version="WKT1_GDAL"),
                "longitude/latitude coordinates in decimal degrees on the WGS 84 spheroid",
            ),
        ],
    )


def _register_srs(connection: sqlite3.Connection, crs: str) -> int:
    code = _epsg_code(crs)
    exists = connection.execute("SELECT 1 FROM gpkg_spatial_ref_sys WHERE srs_id = ?", (code,)).fetchone()
    if not exists:
        connection.execute(
            "INSERT INTO gpkg_spatial_ref_sys VALUES (?, ?, 'EPSG', ?, ?, ?)",
            (crs, code, code, crs_wkt(crs, version="WKT1_GDAL"), f"{crs} coordinate reference system"),
        )
    return code


def _write_layer(connection: sqlite3.Connection, layer: SpatialLayer) -> None:
    table = _identifier(layer.name)
    srs_id = _register_srs(connection, layer.crs)
    fields = sorted({key for feature in layer.features for key in feature.properties})
    for field in fields:
        _identifier(field)
    field_types = {
        field: _field_type([feature.properties.get(field) for feature in layer.features]) for field in fields
    }
    columns = ["fid INTEGER PRIMARY KEY AUTOINCREMENT", '"geom" BLOB NOT NULL']
    columns.extend(f"{_identifier(field)} {field_types[field]}" for field in fields)
    connection.execute(f"CREATE TABLE {table} ({', '.join(columns)})")

    bounds = layer.bounds
    if bounds is None:
        min_x = min_y = max_x = max_y = None
    else:
        min_x, min_y, max_x, max_y = bounds
    connection.execute(
        "INSERT INTO gpkg_contents VALUES (?, 'features', ?, ?, ?, ?, ?, ?, ?, ?)",
        (layer.name, layer.display_name, layer.source, LAST_CHANGE, min_x, min_y, max_x, max_y, srs_id),
    )
    actual_geometry_types = {feature.geometry.geom_type.upper() for feature in layer.features}
    metadata_geometry_type = next(iter(actual_geometry_types)) if len(actual_geometry_types) == 1 else "GEOMETRY"
    if not actual_geometry_types:
        metadata_geometry_type = layer.geometry_type.upper()
    connection.execute(
        "INSERT INTO gpkg_geometry_columns VALUES (?, 'geom', ?, ?, 0, 0)",
        (layer.name, metadata_geometry_type, srs_id),
    )

    column_names = ['"geom"'] + [_identifier(field) for field in fields]
    placeholders = ",".join("?" for _ in column_names)
    sql = f"INSERT INTO {table} ({', '.join(column_names)}) VALUES ({placeholders})"
    for index, feature in enumerate(layer.features):
        values = [_geometry_blob(layer, index)]
        values.extend(_property_value(feature.properties.get(field)) for field in fields)
        connection.execute(sql, values)


def write_geopackage(path: Path, layers: list[SpatialLayer]) -> Path:
    if not layers:
        raise ExportError("GeoPackage에 저장할 레이어가 없습니다.")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    if temporary.exists():
        temporary.unlink()
    connection = sqlite3.connect(temporary)
    try:
        _initialize(connection)
        for layer in layers:
            with connection:
                _write_layer(connection, layer)
        result = connection.execute("PRAGMA integrity_check").fetchone()
        if result != ("ok",):
            raise ExportError("GeoPackage 무결성 검사에 실패했습니다.")
    except (sqlite3.Error, OSError, ValueError) as exc:
        raise ExportError(f"GeoPackage를 저장하지 못했습니다: {exc}") from exc
    finally:
        connection.close()
    if path.exists():
        path.unlink()
    temporary.replace(path)
    return path
