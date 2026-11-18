from pathlib import Path
import zipfile
from xml.etree import ElementTree as ET

import pytest
from shapely.geometry import LineString

from routegis.qgis import project as project_module
from routegis.qgis.project import _layer_id
from routegis.models import SpatialFeature, SpatialLayer


def test_layer_id_remains_stable_when_sha1_is_not_used_for_security() -> None:
    assert _layer_id("route") == "route_fc166eb81361"


def test_headless_writer_uses_supplied_project_relative_paths(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_directory = tmp_path / "project"
    geopackage = project_directory / "assets" / "custom.gpkg"
    source_drawing = project_directory / "input" / "actual.dxf"

    project_file = project_module.write_qgis_project(
        project_directory / "relative.qgz",
        "Relative paths",
        geopackage,
        [
            SpatialLayer(
                "custom_layer",
                "Custom layer",
                "EPSG:5186",
                "Polygon",
                "facility",
            )
        ],
        source_drawing,
        "EPSG:5186",
    )

    with zipfile.ZipFile(project_file) as archive:
        qgs = ET.fromstring(archive.read(archive.namelist()[0]))
    assert qgs.findtext("./properties/SpatialRefSys/ProjectionsEnabled") == "1"
    sources = {
        layer.findtext("layername"): layer.findtext("datasource")
        for layer in qgs.findall("./projectlayers/maplayer")
    }
    assert sources == {
        "Custom layer": "assets/custom.gpkg|layername=custom_layer",
        "원본 CAD": "input/actual.dxf",
        "VWorld Street": (
            "url=https://xdworld.vworld.kr/2d/Base/service/{z}/{x}/{y}.png"
            "&zmax=18&zmin=0&type=xyz&tilePixelRatio=1"
        ),
        "VWorld Hybrid Label": (
            "url=https://xdworld.vworld.kr/2d/Hybrid/service/{z}/{x}/{y}.png"
            "&zmax=18&zmin=0&type=xyz&tilePixelRatio=1"
        ),
        "VWorld Hybrid": (
            "url=https://xdworld.vworld.kr/2d/Satellite/service/{z}/{x}/{y}.jpeg"
            "&zmax=18&zmin=0&type=xyz&tilePixelRatio=1"
        ),
    }


@pytest.mark.parametrize(
    ("feature_counts", "expected_name"),
    [
        ((), "04_수자원"),
        ((0,), "04_수자원 (해당없음)"),
        ((0, 0), "04_수자원 (해당없음)"),
        ((0, 1), "04_수자원"),
        ((1, 0), "04_수자원"),
        ((1, 1), "04_수자원"),
    ],
)
def test_group_marks_no_applicable_features_only_when_all_member_layers_are_empty(
    tmp_path: Path, monkeypatch, feature_counts, expected_name,
):
    line = LineString([(200000, 489013), (201000, 489013)])
    route = SpatialLayer(
        "linework", "선형", "EPSG:5186", "LineString", "route",
        [SpatialFeature(line)],
    )
    water_layers = [
        SpatialLayer(
            f"water_{index}", f"수자원 {index}", "EPSG:5186", "LineString", "water",
            [SpatialFeature(line) for _ in range(count)],
        )
        for index, count in enumerate(feature_counts)
    ]
    project_file = project_module.write_qgis_project(
        tmp_path / "groups.qgz", "Group labels", tmp_path / "data" / "project.gpkg",
        [route, *water_layers], tmp_path / "source" / "drawing.dxf", "EPSG:5186",
    )

    with zipfile.ZipFile(project_file) as archive:
        qgs = ET.fromstring(archive.read(archive.namelist()[0]))
    groups = qgs.findall("./layer-tree-group/layer-tree-group")
    expected_names = list(project_module.GROUPS)
    expected_names[3] = expected_name
    assert [group.attrib["name"] for group in groups] == expected_names
    assert len(groups[3].findall("./layer-tree-layer")) == len(feature_counts)
