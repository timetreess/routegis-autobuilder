import pytest
from shapely.errors import GEOSException
from shapely.geometry import LineString, MultiPolygon, Point
from shapely.geometry.base import BaseGeometry

import routegis.gis.crs as crs_module
from routegis.errors import CrsError
from routegis.gis.crs import KOREAN_PROJECTED_CRS, korea_sanity_issues, transform_geometry, transform_layer
from routegis.gis.geometry import create_aoi_layer, create_linework_layer, create_route_layer
from routegis.gis.validator import validate_route_and_aoi
from routegis.models import SpatialFeature, SpatialLayer


def test_korean_crs_choices_cover_all_domestic_belts_and_web_utm_projections():
    codes = [code for code, _name in KOREAN_PROJECTED_CRS]

    assert len(codes) == len(set(codes))
    assert set(codes) == {
        *(f"EPSG:{code}" for code in range(5167, 5189)),
        "EPSG:2096", "EPSG:2097", "EPSG:2098",
        "EPSG:32651", "EPSG:32652", "EPSG:3857",
    }


@pytest.mark.parametrize(("code", "name"), KOREAN_PROJECTED_CRS)
def test_korean_crs_choices_resolve_to_projected_metre_crs(code, name):
    assert name.strip()
    assert crs_module.normalize_crs(code) == code
    crs_module.validate_projected_metre_crs(code)


def test_crs_transform_and_korea_sanity_check():
    wgs84 = transform_geometry(Point(200000.0, 489012.955691), "EPSG:5186", "EPSG:4326")

    assert wgs84.x == pytest.approx(127.0, abs=1e-6)
    assert wgs84.y == pytest.approx(37.0, abs=1e-6)

    route = create_route_layer(LineString([(200000, 489013), (201000, 489013)]), "EPSG:5186", "CENTER")
    assert korea_sanity_issues(route) == []


def test_transform_bounds_returns_float_bounds_for_equivalent_crs():
    from routegis.gis import transform_bounds

    assert transform_bounds((1, 2, 3, 4), "EPSG:5186", "epsg:5186") == (1.0, 2.0, 3.0, 4.0)


def test_transform_bounds_rejects_negative_densify_points():
    from routegis.gis import transform_bounds

    with pytest.raises(ValueError, match="densify_pts"):
        transform_bounds((1.0, 2.0, 3.0, 4.0), "EPSG:5186", "EPSG:5186", densify_pts=-1)


def test_transform_layer_resolves_each_crs_once(monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []
    normalize_crs = crs_module.normalize_crs

    def tracked_normalize_crs(value: str) -> str:
        calls.append(value)
        return normalize_crs(value)

    monkeypatch.setattr(crs_module, "normalize_crs", tracked_normalize_crs)
    layer = SpatialLayer(
        name="linework",
        display_name="선형",
        crs="EPSG:5186",
        geometry_type="LineString",
        category="route",
        features=[
            SpatialFeature(LineString([(200000 + index, 489013), (200001 + index, 489013)]))
            for index in range(3)
        ],
    )

    transformed = transform_layer(layer, "EPSG:5186")

    assert calls == ["EPSG:5186", "EPSG:5186"]
    assert [feature.geometry for feature in transformed.features] == [
        feature.geometry for feature in layer.features
    ]


def test_transform_layer_preserves_empty_layer_source_crs_behavior():
    layer = SpatialLayer(
        name="empty",
        display_name="빈 레이어",
        crs="invalid-source",
        geometry_type="LineString",
        category="route",
    )

    transformed = transform_layer(layer, "EPSG:5186")

    assert transformed.crs == "EPSG:5186"
    assert transformed.features == []


def test_transform_layer_validates_target_before_source_for_empty_layer():
    layer = SpatialLayer(
        name="empty",
        display_name="빈 레이어",
        crs="invalid-source",
        geometry_type="LineString",
        category="route",
    )

    with pytest.raises(CrsError, match="invalid-target"):
        transform_layer(layer, "invalid-target")


def test_korea_sanity_check_uses_layer_extent_without_geometry_union(monkeypatch: pytest.MonkeyPatch):
    layer = SpatialLayer(
        name="linework",
        display_name="선형",
        crs="EPSG:5186",
        geometry_type="LineString",
        category="route",
        features=[
            SpatialFeature(LineString([(200000, 489013), (201000, 489013)])),
            SpatialFeature(LineString([(201000, 489013), (202000, 489013)])),
        ],
    )

    def fail_union(*_args, **_kwargs):
        raise AssertionError("sanity check must not union every feature")

    monkeypatch.setattr(BaseGeometry, "union", fail_union)

    assert korea_sanity_issues(layer) == []


def test_korea_sanity_check_preserves_feature_envelopes_for_nonlinear_crs():
    coordinates = ((127, 37), (-179, 0), (179, 0), (0, -89), (0, 89))
    layer = SpatialLayer(
        name="global_points",
        display_name="세계 지점",
        crs="EPSG:8857",
        geometry_type="Point",
        category="route",
        features=[
            SpatialFeature(transform_geometry(Point(x, y), "EPSG:4326", "EPSG:8857"))
            for x, y in coordinates
        ],
    )

    assert korea_sanity_issues(layer) == []


def test_buffer_covers_route_and_has_expected_distance():
    route = create_route_layer(LineString([(200000, 489013), (201000, 489013)]), "EPSG:5186", "CENTER")
    aoi = create_aoi_layer(route, 1000)

    assert aoi.features[0].geometry.covers(route.features[0].geometry)
    assert aoi.bounds == pytest.approx((199000, 488013, 202000, 490013))
    assert not [issue for issue in validate_route_and_aoi(route, aoi) if issue.severity == "error"]


def test_buffer_rejects_angular_crs():
    route = create_route_layer(LineString([(127, 37), (127.01, 37)]), "EPSG:4326", "CENTER")

    with pytest.raises(CrsError, match="투영좌표계"):
        create_aoi_layer(route, 1000)


def test_dense_linework_buffer_does_not_require_buffering_the_whole_network(monkeypatch):
    lines = [LineString([(200000, 489013 + index), (200100, 489013 + index)]) for index in range(128)]
    route = create_linework_layer({"DETAIL": lines}, "EPSG:5186")
    original_geometries = [feature.geometry.wkb for feature in route.features]
    buffer = BaseGeometry.buffer

    def memory_limited_buffer(geometry, *args, **kwargs):
        # Reproduce GEOS's allocation failure on a densely overlapping network
        # without letting a regression exhaust the test runner's memory.
        if geometry.geom_type == "MultiLineString":
            raise GEOSException("bad allocation")
        return buffer(geometry, *args, **kwargs)

    monkeypatch.setattr(BaseGeometry, "buffer", memory_limited_buffer)

    aoi = create_aoi_layer(route, 1000)

    assert aoi.features[0].geometry.is_valid
    assert aoi.bounds == pytest.approx((199000, 488013, 201100, 490140))
    assert not [issue for issue in validate_route_and_aoi(route, aoi) if issue.severity == "error"]
    assert [feature.geometry.wkb for feature in route.features] == original_geometries


def test_linework_buffer_preserves_crossings_disconnected_parts_and_holes():
    lines = [
        LineString([(0, 0), (20, 0)]),
        LineString([(10, -10), (10, 10)]),
        LineString([(0, 0), (20, 0)]),
        LineString([(200, 0), (260, 0), (260, 60), (200, 60), (200, 0)]),
    ]
    route = create_linework_layer({"CENTER": lines}, "EPSG:5186")

    aoi = create_aoi_layer(route, 2)
    geometry = aoi.features[0].geometry

    assert geometry.is_valid
    assert isinstance(geometry, MultiPolygon)
    assert len(geometry.geoms) == 2
    assert all(geometry.covers(line) for line in lines)
    assert geometry.covers(Point(10, 11.9))
    assert not geometry.covers(Point(10, 12.1))
    assert not geometry.covers(Point(100, 0))
    assert not geometry.covers(Point(230, 30))
    assert aoi.bounds == pytest.approx((-2, -12, 262, 62))


@pytest.mark.parametrize("mode", ["buffer", "bbox"])
def test_empty_line_geometries_cannot_form_an_analysis_area(mode):
    route = SpatialLayer(
        name="empty", display_name="빈 선형", crs="EPSG:5186",
        geometry_type="LineString", category="route",
        features=[SpatialFeature(LineString())],
    )

    with pytest.raises(CrsError, match="분석 범위"):
        create_aoi_layer(route, 1000, mode=mode)
