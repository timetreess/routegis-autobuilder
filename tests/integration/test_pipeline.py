from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import zipfile
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree as ET

import pytest
from shapely.geometry import LineString, Polygon

from routegis import cli as cli_module
from routegis.app import project_service as project_service_module
from routegis.app.project_service import ProjectService
from routegis.cli import FixtureProvider
from routegis.errors import ConfigurationError, CrsError, ExportError, ProviderError, RouteGISError
from routegis.gis.export import export_layers
from routegis.gis.shapefile import write_shapefile
from routegis.models import (
    DownloadResult,
    LayerDefinition,
    PipelineResult,
    PipelineState,
    ProjectConfig,
    SpatialFeature,
    SpatialLayer,
)
from routegis.qgis import project as qgis_project_module


EXPECTED_QGIS_GROUPS = (
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
QGIS_PROVIDER_PROPERTY_KEYS = {
    "routegis/provider",
    "routegis/publisher",
    "routegis/dataset_id",
    "routegis/attribution",
    "routegis/license_name",
    "routegis/license_url",
}


def _qgis_custom_properties(map_layer: ET.Element) -> dict[str, str]:
    return {
        option.attrib["name"]: option.attrib.get("value", "")
        for option in map_layer.findall("./customproperties/Option/Option")
        if option.attrib.get("name", "").startswith("routegis/")
    }


@pytest.mark.parametrize(
    ("layer", "expected_properties"),
    [
        pytest.param(
            SpatialLayer(
                "ecology_nature",
                "생태자연도",
                "EPSG:5186",
                "MultiPolygon",
                "ecology",
            ),
            {
                "color": "76,175,80,80",
                "outline_color": "46,125,50,255",
                "outline_width": "0.8",
            },
            id="ecology",
        ),
        pytest.param(
            SpatialLayer(
                "heritage_protection",
                "국가유산 지정·보호구역",
                "EPSG:5186",
                "MultiPolygon",
                "heritage",
            ),
            {
                "color": "156,39,176,80",
                "outline_color": "106,27,154,255",
                "outline_width": "0.8",
            },
            id="heritage",
        ),
        pytest.param(
            SpatialLayer(
                "river_centerline",
                "하천 중심선",
                "EPSG:5186",
                "MultiLineString",
                "water",
            ),
            {"line_color": "25,118,210,255", "line_width": "1.2"},
            id="river-line",
        ),
        pytest.param(
            SpatialLayer(
                "river_area",
                "하천구역·수면",
                "EPSG:5186",
                "MultiPolygon",
                "water",
            ),
            {
                "color": "66,165,245,70",
                "outline_color": "25,118,210,255",
                "outline_width": "0.8",
            },
            id="river-area",
        ),
        pytest.param(
            SpatialLayer(
                "power_line",
                "송전선로",
                "EPSG:5186",
                "MultiLineString",
                "power",
            ),
            {"line_color": "141,74,45,255", "line_width": "1.2"},
            id="power-line",
        ),
        pytest.param(
            SpatialLayer(
                "power_support",
                "송전철탑·지지물",
                "EPSG:5186",
                "MultiPoint",
                "power",
            ),
            {"color": "141,74,45,255", "size": "2.5"},
            id="power-support",
        ),
    ],
)
def test_headless_renderer_uses_approved_default_layer_styles(
    layer: SpatialLayer,
    expected_properties: dict[str, str],
):
    map_layer = ET.Element("maplayer")

    qgis_project_module._renderer(map_layer, layer, shapefile=True)

    symbol_layer = map_layer.find("./renderer-v2/symbols/symbol/layer")
    assert symbol_layer is not None
    properties = {
        prop.attrib["k"]: prop.attrib["v"]
        for prop in symbol_layer.findall("./prop")
    }
    assert expected_properties.items() <= properties.items()


def test_headless_renderer_categorizes_land_ownership_by_lbl():
    geometry = Polygon(
        [(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)]
    )
    layer = SpatialLayer(
        "land_ownership",
        "토지소유공간정보",
        "EPSG:5186",
        "MultiPolygon",
        "land",
        [
            SpatialFeature(geometry, {"lbl": "법인 "}, "owner-1"),
            SpatialFeature(geometry, {"lbl": "개인"}, "owner-2"),
            SpatialFeature(geometry, {"lbl": None}, "owner-3"),
        ],
    )
    map_layer = ET.Element("maplayer")

    qgis_project_module._renderer(map_layer, layer, shapefile=True)

    assert float(map_layer.findtext("layerOpacity", "1")) == pytest.approx(0.7)
    renderer = map_layer.find("./renderer-v2")
    assert renderer is not None
    assert renderer.attrib["type"] == "categorizedSymbol"
    assert renderer.attrib["attr"] == "lbl"
    categories = renderer.findall("./categories/category")
    assert [category.attrib["value"] for category in categories] == [
        "",
        "개인",
        "법인",
    ]
    assert [category.attrib["label"] for category in categories] == [
        "미분류",
        "개인",
        "법인",
    ]
    colors = []
    for symbol in renderer.findall("./symbols/symbol"):
        color = symbol.find("./layer/prop[@k='color']")
        assert color is not None
        colors.append(color.attrib["v"])
    assert len(set(colors)) == 3


def test_non_euc_kr_layer_uses_geopackage_without_partial_shapefile(
    tmp_path: Path,
):
    geometry = Polygon(
        [(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)]
    )
    normal = SpatialLayer(
        "normal_land",
        "일반 토지",
        "EPSG:5186",
        "Polygon",
        "land",
        [SpatialFeature(geometry, {"lbl": "개인"}, "normal-1")],
    )
    extended = SpatialLayer(
        "land_ownership",
        "토지소유공간정보",
        "EPSG:5186",
        "Polygon",
        "land",
        [
            SpatialFeature(
                geometry,
                {"lbl": "개인 ", "note": "보존할 문자 😀"},
                "extended-1",
            ),
            SpatialFeature(geometry, {"lbl": None}, "extended-2"),
        ],
    )

    geopackage, mappings = export_layers(tmp_path, [normal, extended])
    project_file = qgis_project_module.write_qgis_project(
        tmp_path / "fallback.qgz",
        "인코딩 대체",
        geopackage,
        [normal, extended],
        tmp_path / "source.dxf",
        "EPSG:5186",
    )

    assert mappings.keys() == {"normal_land"}
    assert (tmp_path / "shp" / "land" / "normal_land.shp").is_file()
    assert not (tmp_path / "shp" / "land" / "land_ownership.shp").exists()
    with zipfile.ZipFile(project_file) as archive:
        qgs = ET.fromstring(archive.read(archive.namelist()[0]))
    sources = {
        layer.findtext("layername"): layer.findtext("datasource")
        for layer in qgs.findall("./projectlayers/maplayer")
    }
    assert sources["일반 토지"] == "shp/land/normal_land.shp"
    assert sources["토지소유공간정보"] == "data/project.gpkg|layername=land_ownership"
    ownership = next(
        layer
        for layer in qgs.findall("./projectlayers/maplayer")
        if layer.findtext("layername") == "토지소유공간정보"
    )
    assert [
        category.attrib["value"]
        for category in ownership.findall("./renderer-v2/categories/category")
    ] == ["", "개인 "]


def test_pipeline_reports_shapefile_encoding_fallback_as_warning(
    tmp_path: Path,
    fixture_directory: Path,
):
    class NonEucKrProvider:
        def fetch_layer(
            self,
            definition: LayerDefinition,
            aoi: SpatialLayer,
        ) -> DownloadResult:
            return DownloadResult(
                layer_id=definition.id,
                status="success",
                layer=SpatialLayer(
                    name=definition.id,
                    display_name=definition.name,
                    crs=aoi.crs,
                    geometry_type=definition.geometry_type,
                    category=definition.category,
                    features=[
                        SpatialFeature(
                            Polygon(
                                [
                                    (199_900, 488_900),
                                    (200_100, 488_900),
                                    (200_100, 489_100),
                                    (199_900, 489_100),
                                    (199_900, 488_900),
                                ]
                            ),
                            {"note": "보존할 문자 😀"},
                            "fallback-1",
                        )
                    ],
                    source=definition.provider,
                ),
            )

    result = ProjectService().build(
        ProjectConfig(
            project_name="인코딩 대체 경고",
            source_drawing=fixture_directory / "sample_route.dxf",
            source_layers=("CENTER",),
            source_crs="EPSG:5186",
            output_crs="EPSG:5186",
            buffer_distance=1000,
            selected_layers=("land_ownership",),
            output_directory=tmp_path,
        ),
        provider=NonEucKrProvider(),
    )

    assert result.state == PipelineState.COMPLETED_WITH_WARNINGS
    assert [issue.code for issue in result.issues] == ["SHAPEFILE_ENCODING_FALLBACK"]
    assert not (
        result.project_directory / "shp" / "land" / "land_ownership.shp"
    ).exists()
    report = json.loads(
        (result.project_directory / "reports" / "processing_report.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["issues"] == [
        {
            "code": "SHAPEFILE_ENCODING_FALLBACK",
            "layer": "land_ownership",
            "message": (
                "EUC-KR로 표현할 수 없는 속성값이 있어 Shapefile 대신 "
                "GeoPackage를 사용합니다."
            ),
            "severity": "warning",
        }
    ]
    with zipfile.ZipFile(result.project_file) as archive:
        qgs = ET.fromstring(archive.read(archive.namelist()[0]))
    ownership = next(
        layer
        for layer in qgs.findall("./projectlayers/maplayer")
        if layer.findtext("layername") == "토지소유공간정보"
    )
    assert ownership.findtext("datasource") == (
        "data/project.gpkg|layername=land_ownership"
    )


def test_spatial_fixture_covers_legacy_and_default_layers(fixture_directory: Path):
    payload = json.loads((fixture_directory / "spatial_mock.json").read_text(encoding="utf-8"))
    expected_geometry_types = {
        "ecology_nature": "MultiPolygon",
        "land_ownership": "MultiPolygon",
        "heritage_protection": "MultiPolygon",
        "power_line": "MultiLineString",
        "power_support": "MultiPoint",
        "river_centerline": "MultiLineString",
        "river_area": "MultiPolygon",
    }
    layers = payload["layers"]

    assert payload["crs"] == "EPSG:5186"
    assert set(layers) == {"cadastral", "building", *expected_geometry_types}
    assert layers["cadastral"]["type"] == "FeatureCollection"
    assert layers["building"] == {"error": "mock timeout fixture-key"}
    assert {
        layer_id: layers[layer_id]["features"][0]["geometry"]["type"]
        for layer_id in expected_geometry_types
    } == expected_geometry_types


def test_golden_pipeline_preserves_successes_and_reports_failures(tmp_path: Path, fixture_directory: Path):
    config = ProjectConfig(
        project_name="샘플 노선 사업",
        source_drawing=fixture_directory / "sample_route.dxf",
        source_layers=("CENTER",),
        source_crs="EPSG:5186",
        output_crs="EPSG:5186",
        buffer_distance=1000,
        selected_layers=("cadastral", "building"),
        output_directory=tmp_path,
    )
    result = ProjectService().build(
        config,
        api_key="fixture-key",
        provider=FixtureProvider(fixture_directory / "spatial_mock.json"),
    )

    assert result.state == PipelineState.COMPLETED_WITH_WARNINGS
    assert result.geopackage.is_file()
    assert result.project_file.is_file()
    assert result.report_html.is_file()
    assert result.manifest.is_file()
    assert result.project_file.parent == result.project_directory
    assert not (result.project_directory / "project").exists()
    assert (result.project_directory / "shp" / "route" / "선형.shp").is_file()
    assert (result.project_directory / "shp" / "cadastral" / "cadastral.shp").is_file()

    manifest = json.loads(result.manifest.read_text(encoding="utf-8"))
    statuses = {layer["id"]: layer["status"] for layer in manifest["layers"]}
    assert statuses == {"building": "failed", "cadastral": "success"}
    assert manifest["outputs"]["project"] == result.project_file.name
    assert "fixture-key" not in result.manifest.read_text(encoding="utf-8")
    assert "fixture-key" not in (result.project_directory / "logs" / "app.log").read_text(encoding="utf-8")
    assert "fixture-key" not in result.report_html.read_text(encoding="utf-8")
    assert "fixture-key" not in (result.project_directory / "reports" / "processing_report.json").read_text(encoding="utf-8")

    with closing(sqlite3.connect(result.geopackage)) as connection:
        layers = {row[0] for row in connection.execute("SELECT table_name FROM gpkg_contents")}
        assert {"linework", "route_buffer", "cadastral"} <= layers
        assert "route_original" not in layers

    with zipfile.ZipFile(result.project_file) as archive:
        names = archive.namelist()
        assert names == [f"{result.project_file.stem}.qgs"]
        qgs_content = archive.read(names[0])
        assert b"data/project.gpkg|layername=" not in qgs_content
        assert "shp/route/선형.shp".encode() in qgs_content
        assert b"../source/sample_route.dxf" not in qgs_content
        assert b"source/sample_route.dxf" in qgs_content
        qgs = ET.fromstring(qgs_content)
        layer_tree_root = qgs.find("./layer-tree-group")
        assert layer_tree_root is not None
        root_children = list(layer_tree_root)
        assert [child.attrib["name"] for child in root_children] == list(EXPECTED_QGIS_GROUPS)
        basemap_group = layer_tree_root.find(
            "./layer-tree-group[@name='11_기본도']"
        )
        assert basemap_group is not None
        assert basemap_group.tag == "layer-tree-group"
        basemap_children = list(basemap_group)
        assert [child.attrib["name"] for child in basemap_children] == [
            "VWorld Street",
            "VWorld Hybrid",
        ]
        vworld_tree_layer = basemap_children[0]
        hybrid_group = basemap_children[1]
        assert vworld_tree_layer.tag == "layer-tree-layer"
        assert vworld_tree_layer.attrib["name"] == "VWorld Street"
        assert vworld_tree_layer.attrib["providerKey"] == "wms"
        assert vworld_tree_layer.attrib["checked"] == "Qt::Checked"
        assert hybrid_group.tag == "layer-tree-group"
        assert hybrid_group.attrib == {
            "name": "VWorld Hybrid",
            "checked": "Qt::Checked",
            "expanded": "1",
        }

        vworld_id = vworld_tree_layer.attrib["id"]
        vworld_layer = next(
            layer
            for layer in qgs.findall("./projectlayers/maplayer")
            if layer.findtext("id") == vworld_id
        )
        expected_uri = (
            "url=https://xdworld.vworld.kr/2d/Base/service/{z}/{x}/{y}.png"
            "&zmax=18&zmin=0&type=xyz&tilePixelRatio=1"
        )
        assert vworld_layer.attrib["type"] == "raster"
        assert vworld_layer.findtext("datasource") == expected_uri
        assert vworld_layer.findtext("layername") == "VWorld Street"
        assert vworld_layer.findtext("srs/spatialrefsys/authid") == "EPSG:3857"
        provider = vworld_layer.find("provider")
        assert provider is not None
        assert provider.text == "wms"
        assert provider.attrib == {"encoding": "UTF-8", "mapUnitsMm": "0"}
        assert vworld_layer.find("renderer-v2") is None

        hybrid_layers = list(hybrid_group)
        expected_hybrid_layers = (
            (
                "VWorld Hybrid Label",
                "url=https://xdworld.vworld.kr/2d/Hybrid/service/{z}/{x}/{y}.png"
                "&zmax=18&zmin=0&type=xyz&tilePixelRatio=1",
            ),
            (
                "VWorld Hybrid",
                "url=https://xdworld.vworld.kr/2d/Satellite/service/{z}/{x}/{y}.jpeg"
                "&zmax=18&zmin=0&type=xyz&tilePixelRatio=1",
            ),
        )
        assert [layer.attrib["name"] for layer in hybrid_layers] == [
            name for name, _uri in expected_hybrid_layers
        ]
        for tree_layer, (name, uri) in zip(hybrid_layers, expected_hybrid_layers):
            assert tree_layer.tag == "layer-tree-layer"
            assert tree_layer.attrib["providerKey"] == "wms"
            assert tree_layer.attrib["checked"] == "Qt::Checked"
            hybrid_layer = next(
                layer
                for layer in qgs.findall("./projectlayers/maplayer")
                if layer.findtext("id") == tree_layer.attrib["id"]
            )
            assert hybrid_layer.attrib["type"] == "raster"
            assert hybrid_layer.findtext("datasource") == uri
            assert hybrid_layer.findtext("layername") == name
            assert hybrid_layer.findtext("srs/spatialrefsys/authid") == "EPSG:3857"
            hybrid_provider = hybrid_layer.find("provider")
            assert hybrid_provider is not None
            assert hybrid_provider.text == "wms"
            assert hybrid_provider.attrib == {"encoding": "UTF-8", "mapUnitsMm": "0"}
            assert hybrid_layer.find("renderer-v2") is None

        assert [layer.attrib["id"] for layer in qgs.findall("./layerorder/layer")[-3:]] == [
            vworld_id,
            *(layer.attrib["id"] for layer in hybrid_layers),
        ]
        assert qgs.find(".//mapcanvas/extent") is not None

    rerun = ProjectService().build(
        replace(config, selected_layers=("building",), export_shapefile=False),
        api_key="fixture-key",
        provider=FixtureProvider(fixture_directory / "spatial_mock.json"),
    )
    assert rerun.state == PipelineState.COMPLETED_WITH_WARNINGS
    assert not (rerun.project_directory / "shp" / "cadastral" / "cadastral.shp").exists()
    assert not (rerun.project_directory / "shp" / "field_mapping.json").exists()
    with closing(sqlite3.connect(rerun.geopackage)) as connection:
        layers = {row[0] for row in connection.execute("SELECT table_name FROM gpkg_contents")}
        assert "cadastral" not in layers


def test_pipeline_accepts_live_layer_from_active_catalog(tmp_path: Path, fixture_directory: Path):
    definition = LayerDefinition(
        id="lt_c_ud801",
        name="개발제한구역",
        provider="vworld",
        service_type="wfs",
        endpoint="https://api.vworld.kr/req/wfs",
        data_id="lt_c_ud801",
        geometry_type="MultiPolygon",
        category="facility",
        request_crs="EPSG:4326",
        response_crs="EPSG:4326",
        publisher="국토교통부 공간정보 오픈플랫폼(VWorld)",
        dataset_id="lt_c_ud801",
        license_name="VWorld OpenAPI 이용조건",
        license_url="https://www.vworld.kr/dev/v4dv_wmsguide2_s001.do",
    )

    class RecordingProvider:
        def __init__(self):
            self.layer_ids: list[str] = []

        def fetch_layer(self, selected: LayerDefinition, aoi: SpatialLayer) -> DownloadResult:
            self.layer_ids.append(selected.id)
            return DownloadResult(
                layer_id=selected.id,
                status="success",
                layer=SpatialLayer(
                    name=selected.id,
                    display_name=selected.name,
                    crs=aoi.crs,
                    geometry_type=selected.geometry_type,
                    category=selected.category,
                    source="fixture",
                ),
            )

    service = ProjectService()
    service.catalog.merge_live([definition])
    provider = RecordingProvider()
    config = ProjectConfig(
        project_name="실시간 카탈로그",
        source_drawing=fixture_directory / "sample_route.dxf",
        source_layers=("CENTER",),
        source_crs="EPSG:5186",
        output_crs="EPSG:5186",
        buffer_distance=1000,
        selected_layers=(definition.id,),
        output_directory=tmp_path,
    )

    result = service.build(config, provider=provider)

    assert provider.layer_ids == [definition.id]
    assert result.downloads[0].layer_id == definition.id
    assert result.downloads[0].status == "success"
    assert result.state == PipelineState.COMPLETED_WITH_WARNINGS
    assert [issue.code for issue in result.issues] == ["EMPTY_LAYER"]
    assert result.project_file is not None
    with zipfile.ZipFile(result.project_file) as archive:
        qgs = ET.fromstring(archive.read(archive.namelist()[0]))
    group = qgs.find("./layer-tree-group/layer-tree-group[@name='08_시설물 (해당없음)']")
    assert group is not None
    assert len(group.findall("./layer-tree-layer")) == 1


def test_pipeline_rejects_geographic_cad_tolerances(tmp_path: Path, fixture_directory: Path):
    config = ProjectConfig(
        project_name="잘못된 좌표계",
        source_drawing=fixture_directory / "sample_route.dxf",
        source_layers=("CENTER",),
        source_crs="EPSG:4326",
        output_crs="EPSG:5186",
        buffer_distance=1000,
        selected_layers=(),
        output_directory=tmp_path,
    )

    with pytest.raises(CrsError, match="투영좌표계"):
        ProjectService().build(config, provider=FixtureProvider(fixture_directory / "spatial_mock.json"))


@pytest.mark.parametrize("output_crs", ["EPSG:5186", "EPSG:3857"])
def test_exported_cad_lengths_match_project_crs(
    tmp_path: Path, fixture_directory: Path, output_crs: str,
):
    result = ProjectService().build(
        ProjectConfig(
            project_name="Reprojected lengths",
            source_drawing=fixture_directory / "sample_route.dxf",
            source_layers=("CENTER",),
            source_crs="EPSG:5186",
            output_crs=output_crs,
            buffer_distance=1000,
            selected_layers=(),
            output_directory=tmp_path,
        )
    )
    manifest = json.loads(result.manifest.read_text(encoding="utf-8"))
    with closing(sqlite3.connect(result.geopackage)) as connection:
        exported_length = connection.execute("SELECT SUM(length_m) FROM linework").fetchone()[0]

    assert exported_length == pytest.approx(manifest["route"]["length_m"])


def test_failed_rerun_preserves_previous_project_and_internal_source(tmp_path: Path, fixture_directory: Path):
    config = ProjectConfig(
        project_name="재실행 안전성",
        source_drawing=fixture_directory / "sample_route.dxf",
        source_layers=("CENTER",),
        source_crs="EPSG:5186",
        output_crs="EPSG:5186",
        buffer_distance=1000,
        selected_layers=(),
        output_directory=tmp_path,
    )
    initial = ProjectService().build(config, provider=FixtureProvider(fixture_directory / "spatial_mock.json"))
    original_manifest = initial.manifest.read_bytes()
    internal_source = initial.project_directory / "source" / "sample_route.dxf"

    with pytest.raises(RouteGISError):
        ProjectService().build(
            replace(config, source_drawing=internal_source, source_layers=("MISSING",)),
            provider=FixtureProvider(fixture_directory / "spatial_mock.json"),
        )

    assert internal_source.is_file()
    assert initial.geopackage.is_file()
    assert initial.project_file.is_file()
    assert initial.manifest.read_bytes() == original_manifest


def test_manifest_hashes_internal_snapshot_if_external_source_is_removed(
    tmp_path: Path,
    fixture_directory: Path,
):
    source = tmp_path / "mutable-source.dxf"
    source.write_bytes((fixture_directory / "sample_route.dxf").read_bytes())
    expected_digest = hashlib.sha256(source.read_bytes()).hexdigest()

    class RemovingProvider:
        def fetch_layer(
            self,
            definition: LayerDefinition,
            aoi: SpatialLayer,
        ) -> DownloadResult:
            source.unlink()
            return DownloadResult(
                layer_id=definition.id,
                status="success",
                layer=SpatialLayer(
                    name=definition.id,
                    display_name=definition.name,
                    crs=aoi.crs,
                    geometry_type=definition.geometry_type,
                    category=definition.category,
                    source=definition.provider,
                ),
            )

    result = ProjectService().build(
        ProjectConfig(
            project_name="원본 스냅샷",
            source_drawing=source,
            source_layers=("CENTER",),
            source_crs="EPSG:5186",
            output_crs="EPSG:5186",
            buffer_distance=1000,
            selected_layers=("land_ownership",),
            output_directory=tmp_path,
        ),
        provider=RemovingProvider(),
    )

    internal_source = result.project_directory / "source" / source.name
    manifest = json.loads(result.manifest.read_text(encoding="utf-8"))
    assert not source.exists()
    assert internal_source.is_file()
    assert hashlib.sha256(internal_source.read_bytes()).hexdigest() == expected_digest
    assert manifest["source"]["sha256"] == expected_digest


def test_interrupted_build_removes_staging_directory(tmp_path: Path, fixture_directory: Path, monkeypatch):
    config = ProjectConfig(
        project_name="중단 정리",
        source_drawing=fixture_directory / "sample_route.dxf",
        source_layers=("CENTER",),
        source_crs="EPSG:5186",
        output_crs="EPSG:5186",
        buffer_distance=1000,
        selected_layers=(),
        output_directory=tmp_path,
    )
    service = ProjectService()

    def interrupt(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(service, "_build_in_directory", interrupt)

    with pytest.raises(KeyboardInterrupt):
        service.build(config)

    assert list(tmp_path.glob(".중단_정리.build-*")) == []


def test_interrupted_staging_setup_removes_staging_directory(tmp_path: Path, fixture_directory: Path, monkeypatch):
    config = ProjectConfig(
        project_name="초기화 중단",
        source_drawing=fixture_directory / "sample_route.dxf",
        source_layers=("CENTER",),
        source_crs="EPSG:5186",
        output_crs="EPSG:5186",
        buffer_distance=1000,
        selected_layers=(),
        output_directory=tmp_path,
    )

    def interrupt(staging: Path):
        (staging / "source").mkdir()
        raise KeyboardInterrupt

    monkeypatch.setattr(project_service_module, "_directories", interrupt)

    with pytest.raises(KeyboardInterrupt):
        ProjectService().build(config)

    assert list(tmp_path.glob(".초기화_중단.build-*")) == []


def test_publish_retries_a_transient_windows_permission_error(tmp_path: Path, monkeypatch):
    project_directory = tmp_path / "project"
    staging = tmp_path / ".project.build-test"
    staging.mkdir()
    (staging / "new.txt").write_text("new", encoding="utf-8")
    original_replace = Path.replace
    staging_attempts = 0

    def flaky_replace(source: Path, target: Path):
        nonlocal staging_attempts
        if source == staging:
            staging_attempts += 1
            if staging_attempts < 3:
                raise PermissionError("temporary scanner lock")
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", flaky_replace)

    project_service_module._publish_project(staging, project_directory)

    assert staging_attempts == 3
    assert (project_directory / "new.txt").read_text(encoding="utf-8") == "new"


@pytest.mark.parametrize("interrupt_after", ["backup", "publish"])
def test_interrupted_publish_restores_previous_project(tmp_path: Path, monkeypatch, interrupt_after: str):
    project_directory = tmp_path / "project"
    staging = tmp_path / ".project.build-test"
    project_directory.mkdir()
    staging.mkdir()
    (project_directory / "old.txt").write_text("old", encoding="utf-8")
    (staging / "new.txt").write_text("new", encoding="utf-8")
    original_replace = Path.replace

    def interrupting_replace(source: Path, target: Path):
        result = original_replace(source, target)
        if interrupt_after == "backup" and source == project_directory:
            raise KeyboardInterrupt
        if interrupt_after == "publish" and source == staging:
            raise KeyboardInterrupt
        return result

    monkeypatch.setattr(Path, "replace", interrupting_replace)

    with pytest.raises(KeyboardInterrupt):
        project_service_module._publish_project(staging, project_directory)

    assert (project_directory / "old.txt").read_text(encoding="utf-8") == "old"
    assert (staging / "new.txt").read_text(encoding="utf-8") == "new"
    assert list(tmp_path.glob(".project.backup-*")) == []


def test_rerun_rejects_safe_name_collision(tmp_path: Path, fixture_directory: Path):
    config = ProjectConfig(
        project_name="서로 다른 이름",
        source_drawing=fixture_directory / "sample_route.dxf",
        source_layers=("CENTER",),
        source_crs="EPSG:5186",
        output_crs="EPSG:5186",
        buffer_distance=1000,
        selected_layers=(),
        output_directory=tmp_path,
    )
    initial = ProjectService().build(config, provider=FixtureProvider(fixture_directory / "spatial_mock.json"))

    with pytest.raises(ConfigurationError, match="다른 RouteGIS 프로젝트"):
        ProjectService().build(
            replace(config, project_name="서로_다른_이름"),
            provider=FixtureProvider(fixture_directory / "spatial_mock.json"),
        )

    assert initial.manifest.is_file()


def test_linear_pipeline_preserves_all_cad_entities_and_uses_selected_layers_for_aoi(tmp_path: Path):
    import ezdxf

    drawing = tmp_path / "multi_object_linework.dxf"
    document = ezdxf.new()
    modelspace = document.modelspace()
    modelspace.add_line((200000, 489000), (201000, 489000), dxfattribs={"layer": "CENTER"})
    modelspace.add_line((201000, 489000), (202000, 489000), dxfattribs={"layer": "CENTER"})
    modelspace.add_line((201000, 489000), (201000, 490000), dxfattribs={"layer": "CENTER"})
    modelspace.add_line((210000, 489000), (211000, 489000), dxfattribs={"layer": "DETACHED"})
    modelspace.add_line((220000, 489000), (221000, 489000), dxfattribs={"layer": "-교량"})
    document.saveas(drawing)
    config = ProjectConfig(
        project_name="선형 전체 객체 보존",
        source_drawing=drawing,
        source_layers=("CENTER",),
        source_crs="EPSG:5186",
        output_crs="EPSG:5186",
        buffer_distance=500,
        selected_layers=(),
        output_directory=tmp_path,
    )

    result = ProjectService().build(config)
    manifest = json.loads(result.manifest.read_text(encoding="utf-8"))

    assert set(manifest["source"]["layers"]) == {"CENTER", "DETACHED", "-교량"}
    assert manifest["route"]["buffer_source_layers"] == ["CENTER"]
    assert manifest["route"]["aoi_bbox"] == pytest.approx([199500, 488500, 202500, 490500])
    assert (result.project_directory / "shp" / "route" / "선형.shp").is_file()
    with closing(sqlite3.connect(result.geopackage)) as connection:
        rows = connection.execute('SELECT dxf_layer FROM "linework" ORDER BY fid').fetchall()
    assert [row[0] for row in rows].count("CENTER") == 3
    assert {row[0] for row in rows} == {"CENTER", "DETACHED", "-교량"}
    assert len(rows) == 5


def test_pipeline_skips_zero_length_cad_entities_and_reports_handles(tmp_path: Path):
    import ezdxf

    drawing = tmp_path / "zero_length_entities.dxf"
    document = ezdxf.new()
    modelspace = document.modelspace()
    modelspace.add_line((200000, 489000), (201000, 489000), dxfattribs={"layer": "CENTER"})
    modelspace.add_line((200000, 488500), (201000, 488500), dxfattribs={"layer": "AUX"})
    zero_line = modelspace.add_line((200100, 488500), (200100, 488500), dxfattribs={"layer": "AUX"})
    zero_polyline = modelspace.add_lwpolyline(
        [(200200, 488500), (200200, 488500)],
        dxfattribs={"layer": "AUX"},
    )
    document.saveas(drawing)
    handles = {zero_line.dxf.handle, zero_polyline.dxf.handle}
    service = ProjectService()

    candidates = service.analyze_drawing(drawing)

    assert {candidate["name"] for candidate in candidates} == {"AUX", "CENTER"}
    assert len(service.last_cad_warnings) == 1
    assert service.last_cad_warnings[0].layer == "AUX"
    assert all(handle in service.last_cad_warnings[0].message for handle in handles)

    result = service.build(
        ProjectConfig(
            project_name="길이 0 객체 제외",
            source_drawing=drawing,
            source_layers=("CENTER",),
            source_crs="EPSG:5186",
            output_crs="EPSG:5186",
            buffer_distance=500,
            selected_layers=(),
            output_directory=tmp_path,
        )
    )

    assert result.state == PipelineState.COMPLETED_WITH_WARNINGS
    cad_issues = [issue for issue in result.issues if issue.code == "SKIPPED_CAD_ENTITY"]
    assert len(cad_issues) == 1
    assert cad_issues[0].severity == "warning"
    assert cad_issues[0].layer == "AUX"
    assert all(handle in cad_issues[0].message for handle in handles)
    log_text = (result.project_directory / "logs" / "app.log").read_text(encoding="utf-8")
    report_text = (result.project_directory / "reports" / "processing_report.json").read_text(encoding="utf-8")
    assert all(handle in log_text for handle in handles)
    assert all(handle in report_text for handle in handles)
    with closing(sqlite3.connect(result.geopackage)) as connection:
        rows = connection.execute('SELECT dxf_layer FROM "linework" ORDER BY fid').fetchall()
    assert [row[0] for row in rows] == ["AUX", "CENTER"]


def test_non_linear_pipeline_buffers_all_supported_objects_without_route_validation(tmp_path: Path):
    import ezdxf

    drawing = tmp_path / "branched_and_disconnected.dxf"
    document = ezdxf.new()
    modelspace = document.modelspace()
    modelspace.add_line((200000, 489000), (201000, 489000), dxfattribs={"layer": "MAIN"})
    modelspace.add_line((201000, 489000), (202000, 489000), dxfattribs={"layer": "MAIN"})
    modelspace.add_line((201000, 489000), (201000, 490000), dxfattribs={"layer": "BRANCH"})
    modelspace.add_line((210000, 489000), (211000, 489000), dxfattribs={"layer": "DETACHED"})
    document.saveas(drawing)
    config = ProjectConfig(
        project_name="비선형 전체 사업 객체",
        source_drawing=drawing,
        source_layers=(),
        source_crs="EPSG:5186",
        output_crs="EPSG:5186",
        buffer_distance=500,
        selected_layers=(),
        output_directory=tmp_path,
        project_type="non_linear",
    )

    result = ProjectService().build(config)
    manifest = json.loads(result.manifest.read_text(encoding="utf-8"))

    assert manifest["project_type"] == "non_linear"
    assert manifest["source"]["layers"] == ["BRANCH", "DETACHED", "MAIN"]
    assert manifest["route"]["buffer_source_layers"] == ["BRANCH", "DETACHED", "MAIN"]
    assert manifest["route"]["bbox"] == pytest.approx([200000, 489000, 211000, 490000])
    assert manifest["route"]["aoi_bbox"] == pytest.approx([199500, 488500, 211500, 490500])
    assert (result.project_directory / "shp" / "route" / "선형.shp").is_file()
    with closing(sqlite3.connect(result.geopackage)) as connection:
        rows = connection.execute('SELECT dxf_layer FROM "linework" ORDER BY fid').fetchall()
    assert [row[0] for row in rows] == ["BRANCH", "DETACHED", "MAIN", "MAIN"]


def _provider_routing_config(
    tmp_path: Path,
    fixture_directory: Path,
    *,
    project_name: str,
    selected_layers: tuple[str, ...],
) -> ProjectConfig:
    return ProjectConfig(
        project_name=project_name,
        source_drawing=fixture_directory / "sample_route.dxf",
        source_layers=("CENTER",),
        source_crs="EPSG:5186",
        output_crs="EPSG:5186",
        buffer_distance=1000,
        selected_layers=selected_layers,
        output_directory=tmp_path,
    )


class _RecordingProvider:
    def __init__(self, provider_name: str, events: list[tuple[str, str]]):
        self.provider_name = provider_name
        self.events = events
        self.closed = False

    def fetch_layer(self, definition: LayerDefinition, aoi: SpatialLayer) -> DownloadResult:
        self.events.append((self.provider_name, definition.id))
        return DownloadResult(
            layer_id=definition.id,
            status="success",
            layer=SpatialLayer(
                name=definition.id,
                display_name=definition.name,
                crs=aoi.crs,
                geometry_type=definition.geometry_type,
                category=definition.category,
                source=definition.provider,
            ),
        )

    def close(self) -> None:
        self.closed = True


def test_pipeline_routes_selected_order_with_lazy_reused_owned_clients(
    tmp_path: Path,
    fixture_directory: Path,
):
    selected_layers = (
        "land_ownership",
        "heritage_protection",
        "power_line",
        "power_support",
        "ecology_nature",
    )
    service = ProjectService()
    creations: list[str] = []
    factory_calls: list[tuple[str, dict[str, object]]] = []
    events: list[tuple[str, str]] = []
    clients: dict[str, _RecordingProvider] = {}

    def fake_factory(provider_name: str, **credentials: object) -> _RecordingProvider:
        creations.append(provider_name)
        factory_calls.append((provider_name, credentials))
        client = _RecordingProvider(provider_name, events)
        clients[provider_name] = client
        return client

    service._provider = fake_factory  # type: ignore[method-assign]
    result = service.build(
        _provider_routing_config(
            tmp_path,
            fixture_directory,
            project_name="공급자 순서와 재사용",
            selected_layers=selected_layers,
        ),
        api_key="vworld-secret",
        data_go_kr_service_key="ecology-secret",
        domain="https://registered.example.com",
    )

    assert [download.layer_id for download in result.downloads] == list(selected_layers)
    assert creations == ["vworld", "osm", "ecology"]
    assert [
        (provider_name, credentials["domain"])
        for provider_name, credentials in factory_calls
    ] == [
        ("vworld", "https://registered.example.com"),
        ("osm", None),
        ("ecology", None),
    ]
    assert [layer_id for _provider_name, layer_id in events] == list(selected_layers)
    assert all(client.closed for client in clients.values())


def test_injected_provider_overrides_every_layer_and_remains_open(
    tmp_path: Path,
    fixture_directory: Path,
):
    selected_layers = ("ecology_nature", "land_ownership", "power_line")
    events: list[tuple[str, str]] = []
    injected = _RecordingProvider("injected", events)
    service = ProjectService()

    def unexpected_factory(*_args: object, **_kwargs: object) -> _RecordingProvider:
        raise AssertionError("injected provider must bypass owned factories")

    service._provider = unexpected_factory  # type: ignore[method-assign]
    result = service.build(
        _provider_routing_config(
            tmp_path,
            fixture_directory,
            project_name="주입 공급자 우선",
            selected_layers=selected_layers,
        ),
        provider=injected,
    )

    assert [download.layer_id for download in result.downloads] == list(selected_layers)
    assert events == [("injected", layer_id) for layer_id in selected_layers]
    assert injected.closed is False


def test_owned_constructor_failure_isolated_and_later_providers_continue_and_close(
    tmp_path: Path,
    fixture_directory: Path,
):
    selected_layers = ("ecology_nature", "land_ownership", "power_line")
    vworld_secret = "vworld-secret"
    ecology_secret = "ecology-secret"
    events: list[tuple[str, str]] = []
    clients: dict[str, _RecordingProvider] = {}

    def fake_factory(provider_name: str, **_credentials: object) -> _RecordingProvider:
        if provider_name == "ecology":
            raise ProviderError(
                f"초기화 실패 {vworld_secret} {ecology_secret} "
                f"https://example.test/wfs?serviceKey={ecology_secret}"
            )
        client = _RecordingProvider(provider_name, events)
        clients[provider_name] = client
        return client

    service = ProjectService()
    service._provider = fake_factory  # type: ignore[method-assign]
    result = service.build(
        _provider_routing_config(
            tmp_path,
            fixture_directory,
            project_name="생성 실패 격리",
            selected_layers=selected_layers,
        ),
        api_key=vworld_secret,
        data_go_kr_service_key=ecology_secret,
    )

    assert [download.status for download in result.downloads] == ["failed", "success", "success"]
    assert [layer_id for _provider_name, layer_id in events] == ["land_ownership", "power_line"]
    assert all(client.closed for client in clients.values())
    assert vworld_secret not in (result.downloads[0].error or "")
    assert ecology_secret not in (result.downloads[0].error or "")
    assert "?" not in (result.downloads[0].error or "")
    log_text = (result.project_directory / "logs" / "app.log").read_text(encoding="utf-8")
    assert vworld_secret not in log_text
    assert ecology_secret not in log_text
    assert "?" not in log_text


@pytest.mark.parametrize(
    ("layer_id", "credentials", "message"),
    [
        ("land_ownership", {}, "VWorld API Key"),
        ("ecology_nature", {"api_key": "vworld-secret"}, "인증키"),
    ],
)
def test_missing_provider_credential_is_a_failed_layer(
    tmp_path: Path,
    fixture_directory: Path,
    layer_id: str,
    credentials: dict[str, str],
    message: str,
):
    result = ProjectService().build(
        _provider_routing_config(
            tmp_path,
            fixture_directory,
            project_name=f"인증 누락 {layer_id}",
            selected_layers=(layer_id,),
        ),
        **credentials,
    )

    assert result.state == PipelineState.COMPLETED_WITH_WARNINGS
    assert len(result.downloads) == 1
    assert result.downloads[0].status == "failed"
    assert message in (result.downloads[0].error or "")


def test_unknown_provider_is_a_failed_layer(tmp_path: Path, fixture_directory: Path):
    unknown = replace(
        ProjectService().catalog.get("land_ownership"),
        id="unknown_provider_layer",
        name="알 수 없는 공급자",
        provider="unknown",
    )

    class StaticCatalog:
        def snapshot(self) -> dict[str, LayerDefinition]:
            return {unknown.id: unknown}

        def select(self, layer_ids: tuple[str, ...]) -> list[LayerDefinition]:
            return [self.snapshot()[layer_id] for layer_id in layer_ids]

    service = ProjectService()
    service.catalog = StaticCatalog()  # type: ignore[assignment]
    result = service.build(
        _provider_routing_config(
            tmp_path,
            fixture_directory,
            project_name="알 수 없는 공급자 격리",
            selected_layers=(unknown.id,),
        )
    )

    assert result.state == PipelineState.COMPLETED_WITH_WARNINGS
    assert len(result.downloads) == 1
    assert result.downloads[0].status == "failed"
    assert "unknown" in (result.downloads[0].error or "")


LAYER_REPORT_FIELDS = {
    "id",
    "name",
    "status",
    "feature_count",
    "error",
    "provider",
    "data_id",
    "dataset_id",
    "publisher",
    "license_name",
    "license_url",
    "initial_tile_count",
    "request_count",
    "successful_tile_count",
    "failed_tile_count",
    "failed_tile_bbox",
}
GEOMETRY_POLICY = "AOI 교차 객체를 원형 보존하며 AOI 밖 Geometry가 함께 보일 수 있습니다."


def test_default_fixture_exports_provider_attribution_and_complete_reports(
    tmp_path: Path,
    fixture_directory: Path,
):
    selected_layers = (
        "ecology_nature",
        "land_ownership",
        "heritage_protection",
        "power_line",
        "power_support",
        "river_centerline",
        "river_area",
    )
    vworld_secret = "manifest-vworld-secret"
    ecology_secret = "manifest-ecology-secret"
    service = ProjectService()
    result = service.build(
        _provider_routing_config(
            tmp_path,
            fixture_directory,
            project_name="다중 공급자 산출물",
            selected_layers=selected_layers,
        ),
        api_key=vworld_secret,
        data_go_kr_service_key=ecology_secret,
        provider=FixtureProvider(fixture_directory / "spatial_mock.json"),
    )

    assert result.state == PipelineState.COMPLETED
    expected_paths = {
        "ecology_nature": "ecology",
        "land_ownership": "land",
        "heritage_protection": "heritage",
        "power_line": "power",
        "power_support": "power",
        "river_centerline": "water",
        "river_area": "water",
    }
    for layer_id, category in expected_paths.items():
        assert (result.project_directory / "shp" / category / f"{layer_id}.shp").is_file()
    for category in ("land", "ecology", "heritage", "water", "power", "cadastral", "facility"):
        assert (result.project_directory / "shp" / category).is_dir()

    definitions = {definition.id: definition for definition in service.catalog.select(selected_layers)}
    with closing(sqlite3.connect(result.geopackage)) as connection:
        contents = {
            table_name: data_type
            for table_name, data_type in connection.execute(
                "SELECT table_name, data_type FROM gpkg_contents"
            )
        }
        assert all(contents[layer_id] == "features" for layer_id in selected_layers)
        assert contents["routegis_layer_metadata"] == "attributes"
        metadata_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(routegis_layer_metadata)")
        }
        assert metadata_columns == {
            "id",
            "name",
            "provider",
            "data_id",
            "dataset_id",
            "publisher",
            "attribution",
            "license_name",
            "license_url",
        }
        metadata_column_order = sorted(metadata_columns)
        metadata_rows = {}
        for row in connection.execute(
            "SELECT " + ",".join(f'\"{column}\"' for column in metadata_column_order)
            + " FROM routegis_layer_metadata ORDER BY id"
        ):
            metadata = dict(zip(metadata_column_order, row))
            metadata_rows[metadata["id"]] = metadata

    assert set(metadata_rows) == set(selected_layers)
    for layer_id, definition in definitions.items():
        expected_metadata = {
            "id": definition.id,
            "name": definition.name,
            "provider": definition.provider,
            "data_id": definition.data_id,
            "dataset_id": definition.dataset_id,
            "publisher": definition.publisher,
            "attribution": (
                "© OpenStreetMap contributors"
                if definition.provider == "osm"
                else definition.publisher
            ),
            "license_name": definition.license_name,
            "license_url": definition.license_url,
        }
        assert metadata_rows[layer_id] == expected_metadata
        sidecar = result.project_directory / "shp" / definition.category / f"{layer_id}.metadata.json"
        assert sidecar.read_text(encoding="utf-8") == json.dumps(
            expected_metadata,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

    manifest = json.loads(result.manifest.read_text(encoding="utf-8"))
    report_path = result.project_directory / "reports" / "processing_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert [row["id"] for row in manifest["layers"]] == list(selected_layers)
    assert manifest["layers"] == report["layers"]
    assert all(set(row) == LAYER_REPORT_FIELDS for row in manifest["layers"])
    assert report["geometry_policy"] == GEOMETRY_POLICY
    assert all(
        row["initial_tile_count"]
        == row["request_count"]
        == row["successful_tile_count"]
        == row["failed_tile_count"]
        == 0
        and row["failed_tile_bbox"] is None
        for row in report["layers"]
    )

    html_text = result.report_html.read_text(encoding="utf-8")
    assert GEOMETRY_POLICY in html_text
    assert "OpenStreetMap contributors" in html_text
    assert "Open Database License (ODbL) 1.0" in html_text
    assert "https://www.openstreetmap.org/copyright" in html_text
    for artifact in (
        result.manifest,
        report_path,
        result.report_html,
        result.project_directory / "logs" / "app.log",
    ):
        text = artifact.read_text(encoding="utf-8")
        assert vworld_secret not in text
        assert ecology_secret not in text

    with zipfile.ZipFile(result.project_file) as archive:
        qgs = ET.fromstring(archive.read(archive.namelist()[0]))
    layer_tree_root = qgs.find("./layer-tree-group")
    assert layer_tree_root is not None
    assert [child.attrib["name"] for child in layer_tree_root] == list(
        EXPECTED_QGIS_GROUPS
    )
    assert [node.attrib["id"] for node in qgs.findall("./layerorder/layer")] == [
        node.attrib["id"] for node in layer_tree_root.iter("layer-tree-layer")
    ]
    expected_groups = {
        "ecology_nature": "02_생태환경",
        "land_ownership": "10_토지",
        "heritage_protection": "03_국가유산",
        "power_line": "05_전력시설",
        "power_support": "05_전력시설",
        "river_centerline": "04_수자원",
        "river_area": "04_수자원",
    }
    tree_layers_by_source = {
        tree_layer.attrib["source"]: (group.attrib["name"], tree_layer)
        for group in layer_tree_root
        for tree_layer in group.findall("./layer-tree-layer")
    }
    map_layers_by_id = {
        map_layer.findtext("id"): map_layer
        for map_layer in qgs.findall("./projectlayers/maplayer")
    }
    provider_map_layer_ids: set[str] = set()
    for layer_id, definition in definitions.items():
        source = f"shp/{definition.category}/{layer_id}.shp"
        group_name, tree_layer = tree_layers_by_source[source]
        assert group_name == expected_groups[layer_id]
        map_layer_id = tree_layer.attrib["id"]
        provider_map_layer_ids.add(map_layer_id)
        assert float(map_layers_by_id[map_layer_id].findtext("layerOpacity", "1")) == pytest.approx(
            0.7 if layer_id == "land_ownership" else 1.0
        )
        properties = _qgis_custom_properties(map_layers_by_id[map_layer_id])
        assert properties == {
            "routegis/provider": definition.provider,
            "routegis/publisher": definition.publisher,
            "routegis/dataset_id": definition.dataset_id,
            "routegis/attribution": (
                "© OpenStreetMap contributors"
                if definition.provider == "osm"
                else definition.publisher
            ),
            "routegis/license_name": definition.license_name,
            "routegis/license_url": definition.license_url,
        }

    for map_layer_id, map_layer in map_layers_by_id.items():
        if map_layer_id not in provider_map_layer_ids:
            assert _qgis_custom_properties(map_layer) == {}

    basemap_group = layer_tree_root.find("./layer-tree-group[@name='11_기본도']")
    assert basemap_group is not None
    assert [child.attrib["name"] for child in basemap_group] == [
        "VWorld Street",
        "VWorld Hybrid",
    ]
    assert all(
        child.attrib["name"] not in {"VWorld Street", "VWorld Hybrid"}
        for child in layer_tree_root
    )


def test_manifest_report_tile_stats_and_failed_layer_exclusion(
    tmp_path: Path,
    fixture_directory: Path,
):
    vworld_secret = "stats-vworld-secret"
    ecology_secret = "stats-ecology-secret"
    fixture = FixtureProvider(fixture_directory / "spatial_mock.json")

    class StatsProvider:
        def fetch_layer(self, definition: LayerDefinition, aoi: SpatialLayer) -> DownloadResult:
            if definition.id == "building":
                return DownloadResult(
                    layer_id=definition.id,
                    status="failed",
                    feature_count=1,
                    layer=SpatialLayer(
                        name=definition.id,
                        display_name=definition.name,
                        crs=aoi.crs,
                        geometry_type=definition.geometry_type,
                        category=definition.category,
                        source="partial-fixture",
                    ),
                    error=(
                        f"failed {vworld_secret} {ecology_secret} "
                        f"https://example.test/features?key={vworld_secret}"
                    ),
                    initial_tile_count=2,
                    request_count=4,
                    successful_tile_count=1,
                    failed_tile_count=1,
                    failed_tile_bbox=(199000.0, 488000.0, 200000.0, 489000.0),
                )
            result = fixture.fetch_layer(definition, aoi)
            result.initial_tile_count = 3
            result.request_count = 5
            result.successful_tile_count = 3
            return result

    result = ProjectService().build(
        _provider_routing_config(
            tmp_path,
            fixture_directory,
            project_name="통계와 원자적 제외",
            selected_layers=("cadastral", "building"),
        ),
        api_key=vworld_secret,
        data_go_kr_service_key=ecology_secret,
        provider=StatsProvider(),
    )

    assert result.state == PipelineState.COMPLETED_WITH_WARNINGS
    manifest = json.loads(result.manifest.read_text(encoding="utf-8"))
    report = json.loads(
        (result.project_directory / "reports" / "processing_report.json").read_text(encoding="utf-8")
    )
    assert manifest["layers"] == report["layers"]
    assert all(set(row) == LAYER_REPORT_FIELDS for row in manifest["layers"])
    rows = {row["id"]: row for row in manifest["layers"]}
    assert rows["cadastral"]["initial_tile_count"] == 3
    assert rows["cadastral"]["request_count"] == 5
    assert rows["cadastral"]["successful_tile_count"] == 3
    assert rows["cadastral"]["failed_tile_count"] == 0
    assert rows["cadastral"]["failed_tile_bbox"] is None
    assert rows["building"]["initial_tile_count"] == 2
    assert rows["building"]["request_count"] == 4
    assert rows["building"]["successful_tile_count"] == 1
    assert rows["building"]["failed_tile_count"] == 1
    assert rows["building"]["failed_tile_bbox"] == [199000.0, 488000.0, 200000.0, 489000.0]
    assert rows["building"]["feature_count"] == 0
    assert result.downloads[1].layer is None
    assert vworld_secret not in (rows["building"]["error"] or "")
    assert ecology_secret not in (rows["building"]["error"] or "")
    assert "?" not in (rows["building"]["error"] or "")

    with closing(sqlite3.connect(result.geopackage)) as connection:
        contents = {row[0] for row in connection.execute("SELECT table_name FROM gpkg_contents")}
        metadata_ids = {
            row[0] for row in connection.execute("SELECT id FROM routegis_layer_metadata")
        }
    assert "cadastral" in contents
    assert "building" not in contents
    assert metadata_ids == {"cadastral"}
    assert (result.project_directory / "shp" / "cadastral" / "cadastral.shp").is_file()
    assert not (result.project_directory / "shp" / "facility" / "building.shp").exists()
    assert not (result.project_directory / "shp" / "facility" / "building.metadata.json").exists()


def _cli_build_arguments(
    tmp_path: Path,
    fixture_directory: Path,
    *extra: str,
) -> list[str]:
    return [
        "build",
        str(fixture_directory / "sample_route.dxf"),
        "--project-name",
        "CLI Task 8",
        "--output",
        str(tmp_path),
        "--route-layer",
        "CENTER",
        "--source-crs",
        "EPSG:5186",
        "--output-crs",
        "EPSG:5186",
        "--buffer",
        "1000",
        "--layers",
        "ecology_nature,land_ownership,power_line",
        *extra,
    ]


def test_cli_rejects_explicit_zero_buffer(
    tmp_path: Path,
    fixture_directory: Path,
    capsys,
):
    arguments = _cli_build_arguments(tmp_path, fixture_directory)
    arguments[arguments.index("--buffer") + 1] = "0"
    arguments.extend(
        ["--fixture", str(fixture_directory / "spatial_mock.json")]
    )

    exit_code = cli_module.main(arguments)

    assert exit_code == 2
    assert "0보다 커야" in capsys.readouterr().err


def test_cli_analyze_rejects_explicit_zero_chord_tolerance(
    fixture_directory: Path,
    capsys,
):
    exit_code = cli_module.main(
        [
            "analyze",
            str(fixture_directory / "sample_route.dxf"),
            "--chord-tolerance",
            "0",
        ]
    )

    assert exit_code == 2
    assert "0보다 커야" in capsys.readouterr().err


def test_cli_fixture_build_needs_no_credentials(
    tmp_path: Path,
    fixture_directory: Path,
    monkeypatch,
    capsys,
):
    def unexpected_loader(*_paths: Path) -> str:
        raise AssertionError("fixture builds must not load credentials")

    captured: dict[str, object] = {}
    original_build = ProjectService.build

    def recording_build(self: ProjectService, config: ProjectConfig, **kwargs: object) -> PipelineResult:
        captured.update(kwargs)
        return original_build(self, config, **kwargs)

    monkeypatch.setattr(cli_module, "load_vworld_api_key", unexpected_loader)
    monkeypatch.setattr(cli_module, "load_data_go_kr_service_key", unexpected_loader, raising=False)
    monkeypatch.setattr(ProjectService, "build", recording_build)

    exit_code = cli_module.main(
        _cli_build_arguments(
            tmp_path,
            fixture_directory,
            "--fixture",
            str(fixture_directory / "spatial_mock.json"),
        )
    )

    output = capsys.readouterr()
    assert exit_code == 0
    assert captured["api_key"] == ""
    assert captured["data_go_kr_service_key"] == ""
    assert isinstance(captured["provider"], FixtureProvider)
    assert "fixture-key" not in output.out
    assert output.err == ""


def test_cli_real_build_loads_both_credentials_independently(
    tmp_path: Path,
    fixture_directory: Path,
    monkeypatch,
    capsys,
):
    vworld_secret = "cli-vworld-secret"
    ecology_secret = "cli-ecology-secret"
    loader_calls: list[tuple[str, tuple[Path, ...]]] = []
    captured: dict[str, object] = {}

    def load_vworld(*paths: Path) -> str:
        loader_calls.append(("vworld", paths))
        return vworld_secret

    def load_ecology(*paths: Path) -> str:
        loader_calls.append(("ecology", paths))
        return ecology_secret

    def fake_build(self: ProjectService, config: ProjectConfig, **kwargs: object) -> PipelineResult:
        captured.update(kwargs)
        project_directory = config.output_directory / "CLI_Task_8"
        return PipelineResult(
            state=PipelineState.COMPLETED,
            project_directory=project_directory,
            project_file=project_directory / "CLI_Task_8.qgz",
            geopackage=project_directory / "data" / "project.gpkg",
            report_html=project_directory / "reports" / "processing_report.html",
        )

    monkeypatch.setattr(cli_module, "load_vworld_api_key", load_vworld)
    monkeypatch.setattr(cli_module, "load_data_go_kr_service_key", load_ecology, raising=False)
    monkeypatch.setattr(ProjectService, "build", fake_build)

    exit_code = cli_module.main(
        _cli_build_arguments(
            tmp_path,
            fixture_directory,
            "--domain",
            "https://registered.example.com",
        )
    )

    output = capsys.readouterr()
    dotenv = Path.cwd() / ".env"
    assert exit_code == 0
    assert loader_calls == [("vworld", (dotenv,)), ("ecology", (dotenv,))]
    assert captured["api_key"] == vworld_secret
    assert captured["data_go_kr_service_key"] == ecology_secret
    assert captured["domain"] == "https://registered.example.com"
    assert vworld_secret not in output.out + output.err
    assert ecology_secret not in output.out + output.err


def test_cli_never_prints_loaded_credentials_on_build_error(
    tmp_path: Path,
    fixture_directory: Path,
    monkeypatch,
    capsys,
):
    vworld_secret = "cli-error-vworld-secret"
    ecology_secret = "cli-error-ecology-secret"

    monkeypatch.setattr(cli_module, "load_vworld_api_key", lambda *_paths: vworld_secret)
    monkeypatch.setattr(
        cli_module,
        "load_data_go_kr_service_key",
        lambda *_paths: ecology_secret,
        raising=False,
    )

    def failing_build(*_args: object, **_kwargs: object) -> PipelineResult:
        raise RouteGISError(
            f"failed {vworld_secret} {ecology_secret} "
            f"https://example.test/data?key={vworld_secret}"
        )

    monkeypatch.setattr(ProjectService, "build", failing_build)

    exit_code = cli_module.main(_cli_build_arguments(tmp_path, fixture_directory))

    output = capsys.readouterr()
    assert exit_code == 2
    assert vworld_secret not in output.out + output.err
    assert ecology_secret not in output.out + output.err
    assert "?" not in output.err


class _SingleDefinitionCatalog:
    def __init__(self, definition: LayerDefinition):
        self.definition = definition

    def snapshot(self) -> dict[str, LayerDefinition]:
        return {self.definition.id: self.definition}

    def select(self, layer_ids: tuple[str, ...]) -> list[LayerDefinition]:
        return [self.snapshot()[layer_id] for layer_id in layer_ids]


@pytest.mark.parametrize(
    ("field", "unsafe_value"),
    [
        ("id", "../../../escaped"),
        ("id", r"..\..\escaped"),
        ("id", "nested/escaped"),
        ("id", r"nested\escaped"),
        ("id", r"C:escaped"),
        ("id", r"C:\escaped"),
        ("id", r"\\server\share\escaped"),
        ("id", "escaped\x00name"),
        ("id", "."),
        ("id", ".."),
        ("category", "../../../escaped"),
        ("category", r"..\..\escaped"),
    ],
)
def test_pipeline_rejects_unsafe_definition_export_components_and_cleans_staging(
    tmp_path: Path,
    fixture_directory: Path,
    field: str,
    unsafe_value: str,
):
    service = ProjectService()
    changes = {field: unsafe_value}
    if field != "id":
        changes["id"] = "safe_export_layer"
    definition = replace(service.catalog.get("land_ownership"), **changes)
    service.catalog = _SingleDefinitionCatalog(definition)  # type: ignore[assignment]
    provider = _RecordingProvider(definition.provider, [])
    config = _provider_routing_config(
        tmp_path,
        fixture_directory,
        project_name="안전하지 않은 경로 차단",
        selected_layers=(definition.id,),
    )

    with pytest.raises(ExportError, match="경로"):
        service.build(config, provider=provider)

    assert not (tmp_path / "안전하지_않은_경로_차단").exists()
    assert list(tmp_path.glob(".안전하지_않은_경로_차단.build-*")) == []
    assert list(tmp_path.rglob("escaped*")) == []


def test_pipeline_rejects_absolute_definition_id_without_external_writes(
    tmp_path: Path,
    fixture_directory: Path,
):
    external_stem = tmp_path / "absolute-escaped"
    service = ProjectService()
    definition = replace(
        service.catalog.get("land_ownership"),
        id=str(external_stem.resolve()),
    )
    service.catalog = _SingleDefinitionCatalog(definition)  # type: ignore[assignment]

    with pytest.raises(ExportError, match="경로"):
        service.build(
            _provider_routing_config(
                tmp_path,
                fixture_directory,
                project_name="절대 경로 차단",
                selected_layers=(definition.id,),
            ),
            provider=_RecordingProvider(definition.provider, []),
        )

    for suffix in (".shp", ".shx", ".dbf", ".cpg", ".prj", ".qpj", ".metadata.json"):
        assert not external_stem.with_suffix(suffix).exists()
    assert list(tmp_path.glob(".절대_경로_차단.build-*")) == []


@pytest.mark.parametrize(
    ("layer_field", "unsafe_value"),
    [
        ("name", "../../../escaped"),
        ("name", r"..\..\escaped"),
        ("category", "../../../escaped"),
        ("category", r"..\..\escaped"),
    ],
)
def test_pipeline_rejects_provider_layer_export_metadata_mismatch(
    tmp_path: Path,
    fixture_directory: Path,
    layer_field: str,
    unsafe_value: str,
):
    class MismatchedProvider:
        def fetch_layer(self, definition: LayerDefinition, aoi: SpatialLayer) -> DownloadResult:
            values = {
                "name": definition.id,
                "category": definition.category,
            }
            values[layer_field] = unsafe_value
            return DownloadResult(
                layer_id=definition.id,
                status="success",
                layer=SpatialLayer(
                    name=values["name"],
                    display_name=definition.name,
                    crs=aoi.crs,
                    geometry_type=definition.geometry_type,
                    category=values["category"],
                    source=definition.provider,
                ),
            )

    config = _provider_routing_config(
        tmp_path,
        fixture_directory,
        project_name="공급자 경로 불일치 차단",
        selected_layers=("land_ownership",),
    )

    with pytest.raises(ExportError, match="공급자 레이어"):
        ProjectService().build(config, provider=MismatchedProvider())

    assert not (tmp_path / "공급자_경로_불일치_차단").exists()
    assert list(tmp_path.glob(".공급자_경로_불일치_차단.build-*")) == []
    assert list(tmp_path.rglob("escaped*")) == []


@pytest.mark.parametrize(
    ("layer_crs", "geometry_type", "geometry", "issue_code"),
    [
        (
            "EPSG:4326",
            "MultiPolygon",
            Polygon(
                [
                    (199_900, 488_900),
                    (200_100, 488_900),
                    (200_100, 489_100),
                    (199_900, 489_100),
                    (199_900, 488_900),
                ]
            ),
            "PROVIDER_CRS_MISMATCH",
        ),
        (
            "EPSG:5186",
            "MultiLineString",
            LineString([(199_900, 489_000), (200_100, 489_000)]),
            "PROVIDER_GEOMETRY_TYPE_MISMATCH",
        ),
    ],
)
def test_pipeline_rejects_provider_output_contract_mismatch(
    tmp_path: Path,
    fixture_directory: Path,
    layer_crs: str,
    geometry_type: str,
    geometry,
    issue_code: str,
):
    class MismatchedProvider:
        def fetch_layer(
            self,
            definition: LayerDefinition,
            _aoi: SpatialLayer,
        ) -> DownloadResult:
            return DownloadResult(
                layer_id=definition.id,
                status="success",
                layer=SpatialLayer(
                    name=definition.id,
                    display_name=definition.name,
                    crs=layer_crs,
                    geometry_type=geometry_type,
                    category=definition.category,
                    features=[SpatialFeature(geometry, {}, "mismatch-1")],
                    source=definition.provider,
                ),
            )

    result = ProjectService().build(
        _provider_routing_config(
            tmp_path,
            fixture_directory,
            project_name=f"공급자 계약 {issue_code}",
            selected_layers=("land_ownership",),
        ),
        provider=MismatchedProvider(),
    )

    assert result.state == PipelineState.COMPLETED_WITH_WARNINGS
    assert result.downloads[0].status == "failed"
    assert result.downloads[0].layer is None
    assert [issue.code for issue in result.issues] == [issue_code]
    with closing(sqlite3.connect(result.geopackage)) as connection:
        contents = {
            row[0]
            for row in connection.execute("SELECT table_name FROM gpkg_contents")
        }
    assert "land_ownership" not in contents


@pytest.mark.parametrize(
    "outside_path",
    [
        Path("..") / "escaped.shp",
        Path(r"..\escaped.shp"),
        Path(r"C:\escaped.shp"),
        Path(r"\\server\share\escaped.shp"),
        Path("bad\x00name.shp"),
    ],
)
def test_shapefile_boundary_rejects_uncontained_paths(
    tmp_path: Path,
    outside_path: Path,
):
    root = tmp_path / "project" / "shp"
    layer = SpatialLayer(
        name="safe_layer",
        display_name="안전 레이어",
        crs="EPSG:5186",
        geometry_type="MultiPolygon",
        category="land",
    )

    with pytest.raises(ExportError, match="Shapefile 경로"):
        write_shapefile(outside_path, layer, root_directory=root)

    assert not root.exists()
    assert list(tmp_path.rglob("escaped*")) == []


def test_shapefile_boundary_preserves_safe_korean_names_and_confines_all_sidecars(
    tmp_path: Path,
):
    root = tmp_path / "project" / "shp"
    target = root / "한글분류" / "안전_레이어.shp"
    layer = SpatialLayer(
        name="안전_레이어",
        display_name="안전 레이어",
        crs="EPSG:5186",
        geometry_type="MultiPolygon",
        category="한글분류",
    )

    write_shapefile(
        target,
        layer,
        root_directory=root,
        metadata={"publisher": "안전한 발행기관"},
    )

    expected = {
        target.with_suffix(suffix)
        for suffix in (".shp", ".shx", ".dbf", ".cpg", ".prj", ".qpj", ".metadata.json")
    }
    assert all(path.is_file() for path in expected)
    for path in expected:
        path.resolve().relative_to(root.resolve())


def _encoded_secret_forms(secret: str) -> tuple[str, ...]:
    encoded = quote(secret, safe="")
    lowercase_encoded = encoded.lower()
    mixed_case_encoded = encoded.replace("%2F", "%2f").replace("%3F", "%3f")
    return (
        secret,
        encoded,
        lowercase_encoded,
        mixed_case_encoded,
        quote(encoded, safe=""),
        quote(lowercase_encoded, safe=""),
    )


@pytest.mark.parametrize("encoded_input", [False, True])
def test_pipeline_redacts_mixed_case_and_nested_encoded_secrets_from_all_artifacts(
    tmp_path: Path,
    fixture_directory: Path,
    encoded_input: bool,
):
    secret = "a/b+?=%"
    secret_forms = _encoded_secret_forms(secret)

    class EncodedFailureProvider:
        def fetch_layer(self, definition: LayerDefinition, aoi: SpatialLayer) -> DownloadResult:
            return DownloadResult(
                layer_id=definition.id,
                status="failed",
                error="encoded credential forms: " + " | ".join(secret_forms),
            )

    result = ProjectService().build(
        _provider_routing_config(
            tmp_path,
            fixture_directory,
            project_name="인코딩 자격증명 마스킹",
            selected_layers=("land_ownership",),
        ),
        api_key=quote(secret, safe="") if encoded_input else secret,
        provider=EncodedFailureProvider(),
    )

    artifacts = (
        result.manifest,
        result.project_directory / "reports" / "processing_report.json",
        result.report_html,
        result.project_directory / "logs" / "app.log",
    )
    for artifact in artifacts:
        text = artifact.read_text(encoding="utf-8")
        assert "***" in text
        assert all(secret_form not in text for secret_form in secret_forms)


@pytest.mark.parametrize(
    ("message", "arguments"),
    [
        ("tuple values: %s %s", ("a%2fb%2b%3f%3d%25", "a%252fb%252b%253f%253d%2525")),
        (
            "mapping values: %(encoded)s %(nested)s",
            {
                "encoded": "a%2Fb%2B%3f%3D%25",
                "nested": "a%252Fb%252B%253f%253D%2525",
            },
        ),
    ],
)
def test_secret_filter_redacts_encoded_secrets_in_structured_log_arguments(
    message: str,
    arguments: tuple[str, ...] | dict[str, str],
):
    record = logging.LogRecord("routegis.test", logging.WARNING, __file__, 1, message, arguments, None)

    assert project_service_module._SecretFilter("a/b+?=%").filter(record)

    rendered = record.getMessage()
    assert "***" in rendered
    assert "%2f" not in rendered.lower()
    assert "%252f" not in rendered.lower()


def test_cli_redacts_mixed_case_and_nested_encoded_secrets_from_stderr(
    tmp_path: Path,
    fixture_directory: Path,
    monkeypatch,
    capsys,
):
    secret = "a/b+?=%"
    secret_forms = _encoded_secret_forms(secret)
    monkeypatch.setattr(cli_module, "load_vworld_api_key", lambda *_paths: secret)
    monkeypatch.setattr(cli_module, "load_data_go_kr_service_key", lambda *_paths: "", raising=False)

    def failing_build(*_args: object, **_kwargs: object) -> PipelineResult:
        raise RouteGISError("encoded credential forms: " + " | ".join(secret_forms))

    monkeypatch.setattr(ProjectService, "build", failing_build)

    exit_code = cli_module.main(_cli_build_arguments(tmp_path, fixture_directory))

    output = capsys.readouterr()
    assert exit_code == 2
    assert "***" in output.err
    assert all(secret_form not in output.out + output.err for secret_form in secret_forms)


def test_redaction_preserves_ordinary_case_sensitive_text_semantics():
    message = "CaseToken casetoken CASETOKEN"

    assert project_service_module._redact(message, "CaseToken") == "*** casetoken CASETOKEN"
    assert cli_module._redact_error(message, "CaseToken") == "*** casetoken CASETOKEN"
