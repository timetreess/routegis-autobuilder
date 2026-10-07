import sqlite3
import struct
from pathlib import Path

import pytest
from shapely.geometry import LineString, Polygon

from routegis.errors import ExportError
from routegis.gis.export import export_layers, shapefile_export_path
from routegis.gis.gpkg import write_geopackage
from routegis.gis.shapefile import SHAPEFILE_SUFFIXES, write_shapefile
from routegis.models import SpatialFeature, SpatialLayer
from routegis.qgis.project_schema import _vector_source


def _layers():
    route = SpatialLayer(
        "route_centerline",
        "노선 중심선",
        "EPSG:5186",
        "LineString",
        "route",
        [SpatialFeature(LineString([(200000, 489013), (201000, 489013)]), {"length_m": 1000.0}, "r1")],
    )
    parcel = SpatialLayer(
        "cadastral",
        "연속지적도",
        "EPSG:5186",
        "MultiPolygon",
        "cadastral",
        [
            SpatialFeature(
                Polygon([(199900, 488900), (200100, 488900), (200100, 489100), (199900, 489100)]),
                {"긴한글속성명": "테스트", "parcel_id": 1},
                "p1",
            )
        ],
    )
    return route, parcel


def test_geopackage_core_tables_and_geometry_header(tmp_path: Path):
    path = write_geopackage(tmp_path / "project.gpkg", list(_layers()))

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA application_id").fetchone() == (1196444487,)
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        required_srs = {row[0] for row in connection.execute("SELECT srs_id FROM gpkg_spatial_ref_sys")}
        assert {-1, 0, 4326} <= required_srs
        names = {row[0] for row in connection.execute("SELECT table_name FROM gpkg_contents")}
        assert names == {"route_centerline", "cadastral"}
        blob = connection.execute("SELECT geom FROM route_centerline").fetchone()[0]
        assert blob[:2] == b"GP"
        assert blob[3] == 3
        assert struct.unpack("<i", blob[4:8]) == (5186,)


def test_shapefile_headers_records_and_field_mapping(tmp_path: Path):
    _, parcel = _layers()
    path = tmp_path / "cadastral.shp"
    mapping = write_shapefile(path, parcel)

    data = path.read_bytes()
    index = path.with_suffix(".shx").read_bytes()
    dbf = path.with_suffix(".dbf").read_bytes()
    assert struct.unpack(">i", data[:4]) == (9994,)
    assert struct.unpack("<i", data[32:36]) == (5,)
    assert struct.unpack(">i", index[:4]) == (9994,)
    assert struct.unpack("<I", dbf[4:8]) == (1,)
    assert dbf[29] == 0x79
    assert "테스트".encode("euc-kr") in dbf
    assert "테스트".encode("utf-8") not in dbf
    assert path.with_suffix(".cpg").read_text(encoding="ascii").strip() == "EUC-KR"
    assert mapping["긴한글속성명"].startswith("FIELD")


def test_shapefile_rejects_non_euc_kr_before_writing_partial_files(tmp_path: Path):
    _, parcel = _layers()
    parcel.features[0].properties["memo"] = "보존할 문자 😀"
    path = tmp_path / "cadastral.shp"

    with pytest.raises(ExportError, match="EUC-KR"):
        write_shapefile(path, parcel)

    assert all(not path.with_suffix(suffix).exists() for suffix in SHAPEFILE_SUFFIXES)


def test_shapefile_export_path_rejects_layer_path_traversal(tmp_path: Path):
    layer = SpatialLayer(
        "../outside",
        "외부 경로",
        "EPSG:5186",
        "Polygon",
        "land",
    )

    with pytest.raises(ExportError, match="경로 구성요소"):
        shapefile_export_path(tmp_path, layer)


@pytest.mark.parametrize(
    "name",
    [
        "CON",
        "nul.txt",
        "CONIN$",
        "CONOUT$",
        "COM¹",
        "LPT³",
        "CON .txt",
        "alternate:data",
        "trailing.",
        "trailing ",
        "wild?card",
    ],
)
def test_shapefile_export_path_rejects_unsafe_windows_names(
    tmp_path: Path,
    name: str,
):
    layer = SpatialLayer(name, "잘못된 경로", "EPSG:5186", "Polygon", "land")

    with pytest.raises(ExportError, match="경로 구성요소"):
        shapefile_export_path(tmp_path, layer)


def test_all_non_euc_kr_layers_fall_back_without_shapefile_directory_error(
    tmp_path: Path,
):
    _, parcel = _layers()
    parcel.features[0].properties["memo"] = "보존할 문자 😀"

    geopackage, mappings = export_layers(tmp_path, [parcel])

    assert geopackage.is_file()
    assert mappings == {}
    assert (tmp_path / "shp" / "field_mapping.json").is_file()
    path = shapefile_export_path(tmp_path, parcel)
    assert all(not path.with_suffix(suffix).exists() for suffix in SHAPEFILE_SUFFIXES)


def test_non_euc_kr_fallback_removes_stale_shapefile(tmp_path: Path):
    _, parcel = _layers()
    export_layers(tmp_path, [parcel])
    path = shapefile_export_path(tmp_path, parcel)
    assert path.is_file()
    parcel.features[0].properties["memo"] = "보존할 문자 😀"

    _, mappings = export_layers(tmp_path, [parcel])

    assert mappings == {}
    assert all(not path.with_suffix(suffix).exists() for suffix in SHAPEFILE_SUFFIXES)


def test_export_rejects_layers_with_same_shapefile_path(tmp_path: Path):
    linework = SpatialLayer("linework", "선형", "EPSG:5186", "LineString", "route")
    named_line = SpatialLayer("선형", "동명 선형", "EPSG:5186", "LineString", "route")

    with pytest.raises(ExportError, match="동일한 Shapefile 경로"):
        export_layers(tmp_path, [linework, named_line])

    assert not (tmp_path / "data" / "project.gpkg").exists()


def test_vector_source_supports_relative_project_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.chdir(tmp_path)
    route, _ = _layers()
    geopackage, _ = export_layers(Path("relative-project"), [route])

    source = _vector_source(Path("relative-project"), geopackage, route)

    assert source.relative_uri == "shp/route/route_centerline.shp"
    assert Path(source.absolute_uri).is_absolute()
