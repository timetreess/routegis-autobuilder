from __future__ import annotations

from xml.etree import ElementTree as ET

import pytest
from shapely.geometry import Polygon

from routegis import styles
from routegis.models import SpatialFeature, SpatialLayer
from routegis.qgis.qml import qml_text, write_qml_files
from routegis.qgis.renderer_symbols import symbol_properties
from routegis.qgis.renderers import add_renderer
from routegis.web_map import _layer_style


LAYERS = [
    SpatialLayer("linework", "선", "EPSG:5186", "MultiLineString", "route"),
    SpatialLayer("route_buffer", "버퍼", "EPSG:5186", "MultiPolygon", "route"),
    SpatialLayer("river_centerline", "하천", "EPSG:5186", "MultiLineString", "water"),
    SpatialLayer("ecology_nature", "생태", "EPSG:5186", "MultiPolygon", "ecology"),
    SpatialLayer("cadastral", "지적", "EPSG:5186", "MultiPolygon", "cadastral"),
    SpatialLayer("landuse_zone", "용도", "EPSG:5186", "MultiPolygon", "landuse"),
    SpatialLayer("building", "건물", "EPSG:5186", "MultiPolygon", "facility"),
    SpatialLayer("power_support", "철탑", "EPSG:5186", "MultiPoint", "power"),
    SpatialLayer("other_points", "점", "EPSG:5186", "MultiPoint", "facility"),
    SpatialLayer("road", "도로", "EPSG:5186", "MultiLineString", "facility"),
]


def _qgis_props(layer: SpatialLayer) -> dict[str, str]:
    parent = ET.Element("maplayer")
    add_renderer(parent, layer)
    return {
        prop.attrib["k"]: prop.attrib["v"]
        for prop in parent.findall("./renderer-v2/symbols/symbol/layer/prop")
    }


@pytest.mark.parametrize("layer", LAYERS, ids=lambda layer: layer.name)
def test_qgis_and_web_styles_come_from_the_same_definition(layer: SpatialLayer) -> None:
    symbol = styles.symbol_style(layer)
    assert _qgis_props(layer) == dict(symbol_properties(symbol))
    assert _layer_style(layer)["default"] == styles.leaflet_style(symbol)


def test_land_ownership_categories_match_between_qgis_and_web() -> None:
    geometry = Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)])
    layer = SpatialLayer(
        "land_ownership", "토지", "EPSG:5186", "MultiPolygon", "land",
        [SpatialFeature(geometry, {"lbl": "개인"}, "1"), SpatialFeature(geometry, {"lbl": None}, "2")],
    )
    parent = ET.Element("maplayer")
    add_renderer(parent, layer)
    categories = parent.findall("./renderer-v2/categories/category")
    web = _layer_style(layer)
    assert web["field"] == styles.LAND_OWNERSHIP_FIELD
    assert [(c.attrib["value"], c.attrib["label"]) for c in categories] == [
        (item["value"], item["label"]) for item in web["categories"]
    ]
    assert float(parent.findtext("layerOpacity")) == styles.LAND_OWNERSHIP_OPACITY


def test_generated_qml_files_match_style_data(tmp_path) -> None:
    write_qml_files(tmp_path)
    expected = {key for key in styles.STYLES if not key.startswith("default_")}
    assert {path.stem for path in tmp_path.glob("*.qml")} == expected
    for key in expected:
        text = (tmp_path / f"{key}.qml").read_text(encoding="utf-8")
        assert text == qml_text(key)
        root = ET.fromstring(text.split("\n", 1)[1])
        options = {
            item.attrib["name"]: item.attrib["value"]
            for item in root.findall(".//layer/Option/Option")
        }
        assert options == dict(symbol_properties(styles.STYLES[key]))
