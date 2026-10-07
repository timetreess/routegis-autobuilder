from __future__ import annotations

import json
import re
import struct
from pathlib import Path, PureWindowsPath
from typing import Any, Mapping, NamedTuple

from shapely.geometry import LineString, MultiLineString, MultiPoint, MultiPolygon, Point, Polygon
from shapely.geometry.polygon import orient

from ..errors import ExportError
from ..models import SpatialLayer
from .crs import crs_wkt


SHAPEFILE_SUFFIXES = (".shp", ".shx", ".dbf", ".cpg", ".prj", ".qpj", ".metadata.json")
DBF_ENCODING = "euc-kr"
DBF_LANGUAGE_DRIVER = 0x79
WINDOWS_RESERVED_NAMES = {
    "CON",
    "CONIN$",
    "CONOUT$",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
    *(f"{prefix}{suffix}" for prefix in ("COM", "LPT") for suffix in "¹²³"),
}


class DbfEncodingError(ExportError):
    pass


def validate_export_path_component(value: str, *, label: str) -> str:
    windows_path = PureWindowsPath(value)
    device_name = value.split(".", 1)[0].rstrip(" .").upper()
    if (
        not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or re.search(r'[<>:"|?*\x00-\x1f]', value) is not None
        or value.endswith((" ", "."))
        or device_name in WINDOWS_RESERVED_NAMES
        or bool(windows_path.drive)
        or bool(windows_path.root)
    ):
        raise ExportError(f"{label} 경로 구성요소가 올바르지 않습니다: {value!r}")
    return value


def _confined_shapefile_path(path: Path, root_directory: Path | None) -> Path:
    if "\x00" in str(path):
        raise ExportError("Shapefile 경로에 NUL 문자를 사용할 수 없습니다.")
    requested_path = Path(path).with_suffix(".shp")
    root = Path(root_directory) if root_directory is not None else requested_path.parent
    resolved_root = root.resolve(strict=False)
    resolved_path = requested_path.resolve(strict=False)
    try:
        for suffix in SHAPEFILE_SUFFIXES:
            resolved_path.with_suffix(suffix).relative_to(resolved_root)
    except ValueError as exc:
        raise ExportError(
            f"Shapefile 경로가 허용된 디렉터리를 벗어났습니다: {requested_path}"
        ) from exc
    return resolved_path


class DbfField(NamedTuple):
    original: str
    exported: str
    kind: str
    width: int
    decimals: int = 0


def _shape_type(layer: SpatialLayer) -> int:
    geometry_type = layer.geometry_type.upper()
    if geometry_type == "MULTIPOINT":
        return 8
    base = geometry_type.replace("MULTI", "")
    if base == "POINT":
        return 1
    if base in {"LINESTRING", "LINE"}:
        return 3
    if base == "POLYGON":
        return 5
    raise ExportError(f"Shapefile로 내보낼 수 없는 Geometry 형식입니다: {layer.geometry_type}")


def _record_parts(geometry: Any, shape_type: int) -> tuple[list[int], list[tuple[float, float]]]:
    if shape_type == 1:
        if not isinstance(geometry, Point):
            raise ExportError(f"Point 레이어에 {geometry.geom_type} 객체가 있습니다.")
        return [0], [(geometry.x, geometry.y)]
    if shape_type == 8:
        if not isinstance(geometry, MultiPoint):
            raise ExportError(f"MultiPoint 레이어에 {geometry.geom_type} 객체가 있습니다.")
        return [0], [(point.x, point.y) for point in geometry.geoms]
    if shape_type == 3:
        if isinstance(geometry, LineString):
            lines = [geometry]
        elif isinstance(geometry, MultiLineString):
            lines = list(geometry.geoms)
        else:
            raise ExportError(f"Line 레이어에 {geometry.geom_type} 객체가 있습니다.")
        parts: list[int] = []
        points: list[tuple[float, float]] = []
        for line in lines:
            parts.append(len(points))
            points.extend((float(x), float(y)) for x, y, *_ in line.coords)
        return parts, points
    if isinstance(geometry, Polygon):
        polygons = [geometry]
    elif isinstance(geometry, MultiPolygon):
        polygons = list(geometry.geoms)
    else:
        raise ExportError(f"Polygon 레이어에 {geometry.geom_type} 객체가 있습니다.")
    parts = []
    points = []
    for polygon in polygons:
        polygon = orient(polygon, sign=-1.0)
        for ring in [polygon.exterior, *polygon.interiors]:
            parts.append(len(points))
            coordinates = [(float(x), float(y)) for x, y, *_ in ring.coords]
            if coordinates[0] != coordinates[-1]:
                coordinates.append(coordinates[0])
            points.extend(coordinates)
    return parts, points


def _record_content(geometry: Any, shape_type: int) -> bytes:
    parts, points = _record_parts(geometry, shape_type)
    if shape_type == 1:
        return struct.pack("<idd", shape_type, points[0][0], points[0][1])
    min_x = min(point[0] for point in points)
    min_y = min(point[1] for point in points)
    max_x = max(point[0] for point in points)
    max_y = max(point[1] for point in points)
    if shape_type == 8:
        return struct.pack(
            "<i4di",
            shape_type,
            min_x,
            min_y,
            max_x,
            max_y,
            len(points),
        ) + b"".join(struct.pack("<2d", x, y) for x, y in points)
    content = struct.pack("<i4d2i", shape_type, min_x, min_y, max_x, max_y, len(parts), len(points))
    content += struct.pack(f"<{len(parts)}i", *parts)
    content += b"".join(struct.pack("<2d", x, y) for x, y in points)
    return content


def _header(file_length_words: int, shape_type: int, bounds: tuple[float, float, float, float]) -> bytes:
    min_x, min_y, max_x, max_y = bounds
    return struct.pack(">7i", 9994, 0, 0, 0, 0, 0, file_length_words) + struct.pack(
        "<2i8d", 1000, shape_type, min_x, min_y, max_x, max_y, 0.0, 0.0, 0.0, 0.0
    )


def _ascii_field_name(name: str, index: int, used: set[str]) -> str:
    candidate = re.sub(r"[^A-Za-z0-9_]", "", name)[:10] or f"FIELD{index}"
    base = candidate[:8]
    suffix = 1
    while candidate.casefold() in used:
        candidate = f"{base}{suffix:02d}"[:10]
        suffix += 1
    used.add(candidate.casefold())
    return candidate


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return str(value)


def _encode_dbf_text(value: str) -> bytes:
    try:
        return value.encode(DBF_ENCODING)
    except UnicodeEncodeError as exc:
        raise DbfEncodingError(
            f"EUC-KR로 저장할 수 없는 DBF 문자열이 있습니다: {value!r}"
        ) from exc


def _dbf_fields(layer: SpatialLayer) -> list[DbfField]:
    names = sorted({key for feature in layer.features for key in feature.properties})
    used: set[str] = set()
    fields: list[DbfField] = []
    for index, name in enumerate(names, start=1):
        values = [feature.properties.get(name) for feature in layer.features if feature.properties.get(name) is not None]
        exported = _ascii_field_name(name, index, used)
        if values and all(isinstance(value, bool) for value in values):
            fields.append(DbfField(name, exported, "L", 1))
        elif values and all(isinstance(value, int) and not isinstance(value, bool) for value in values):
            width = min(max(max(len(str(value)) for value in values), 1), 20)
            fields.append(DbfField(name, exported, "N", width))
        elif values and all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values):
            fields.append(DbfField(name, exported, "N", 24, 8))
        else:
            width = min(
                max((len(_encode_dbf_text(_stringify(value))) for value in values), default=1),
                254,
            )
            fields.append(DbfField(name, exported, "C", width))
    return fields


def _encode_text(value: str, width: int) -> bytes:
    encoded = _encode_dbf_text(value)
    while len(encoded) > width:
        value = value[:-1]
        encoded = _encode_dbf_text(value)
    return encoded.ljust(width, b" ")


def _dbf_value(value: Any, field: DbfField) -> bytes:
    if value is None:
        return b" " * field.width
    if field.kind == "L":
        return (b"T" if bool(value) else b"F").ljust(field.width, b" ")
    if field.kind == "N":
        rendered = f"{float(value):.{field.decimals}f}" if field.decimals else str(int(value))
        encoded = rendered.encode("ascii")
        if len(encoded) > field.width:
            raise ExportError(f"DBF 숫자 필드 폭을 초과했습니다: {field.original}")
        return encoded.rjust(field.width, b" ")
    return _encode_text(_stringify(value), field.width)


def _write_dbf(path: Path, layer: SpatialLayer, fields: list[DbfField]) -> None:
    header_length = 32 + len(fields) * 32 + 1
    record_length = 1 + sum(field.width for field in fields)
    with path.open("wb") as stream:
        header = bytearray(
            struct.pack(
                "<BBBBIHH20x", 3, 80, 1, 1, len(layer.features), header_length, record_length
            )
        )
        header[29] = DBF_LANGUAGE_DRIVER
        stream.write(header)
        for field in fields:
            name = field.exported.encode("ascii")[:10].ljust(11, b"\x00")
            stream.write(struct.pack("<11sc4xBB14x", name, field.kind.encode("ascii"), field.width, field.decimals))
        stream.write(b"\r")
        for feature in layer.features:
            stream.write(b" ")
            for field in fields:
                stream.write(_dbf_value(feature.properties.get(field.original), field))
        stream.write(b"\x1a")


def write_shapefile(
    path: Path,
    layer: SpatialLayer,
    *,
    root_directory: Path | None = None,
    metadata: Mapping[str, str] | None = None,
) -> dict[str, str]:
    path = _confined_shapefile_path(path, root_directory)
    fields = _dbf_fields(layer)
    path.parent.mkdir(parents=True, exist_ok=True)
    shape_type = _shape_type(layer)
    if any(feature.geometry.is_empty or not feature.geometry.is_valid for feature in layer.features):
        raise ExportError(f"유효하지 않은 Geometry는 Shapefile에 저장할 수 없습니다: {layer.name}")
    contents = [_record_content(feature.geometry, shape_type) for feature in layer.features]
    bounds = layer.bounds or (0.0, 0.0, 0.0, 0.0)
    shp_length_words = 50 + sum(4 + len(content) // 2 for content in contents)
    shx_length_words = 50 + len(contents) * 4

    with path.open("wb") as shp, path.with_suffix(".shx").open("wb") as shx:
        shp.write(_header(shp_length_words, shape_type, bounds))
        shx.write(_header(shx_length_words, shape_type, bounds))
        offset_words = 50
        for record_number, content in enumerate(contents, start=1):
            content_words = len(content) // 2
            shp.write(struct.pack(">2i", record_number, content_words))
            shp.write(content)
            shx.write(struct.pack(">2i", offset_words, content_words))
            offset_words += 4 + content_words

    _write_dbf(path.with_suffix(".dbf"), layer, fields)
    path.with_suffix(".cpg").write_text("EUC-KR\n", encoding="ascii")
    path.with_suffix(".prj").write_text(crs_wkt(layer.crs, version="WKT1_ESRI"), encoding="utf-8")
    path.with_suffix(".qpj").write_text(crs_wkt(layer.crs), encoding="utf-8")
    if metadata is not None:
        path.with_suffix(".metadata.json").write_text(
            json.dumps(dict(metadata), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    return {field.original: field.exported for field in fields}
