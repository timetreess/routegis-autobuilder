from dataclasses import FrozenInstanceError

import pytest
from shapely.geometry import Point, Polygon, box

from routegis.errors import CrsError
from routegis.gis import AoiTile, AoiTilePlanner, compute_request_budget, transform_geometry
from routegis.models import SpatialFeature, SpatialLayer


def _aoi(geometry, crs: str = "EPSG:5186") -> SpatialLayer:
    return SpatialLayer(
        name="aoi",
        display_name="분석 범위",
        crs=crs,
        geometry_type="Polygon",
        category="aoi",
        features=[SpatialFeature(geometry, {"source": "test"})],
    )


def test_aoi_tile_is_a_frozen_value_object():
    tile = AoiTile(
        output_bounds=(0.0, 1.0, 2.0, 3.0),
        request_bounds=(4.0, 5.0, 6.0, 7.0),
    )

    assert tile.depth == 0
    with pytest.raises(FrozenInstanceError):
        setattr(tile, "depth", 1)


@pytest.mark.parametrize(
    ("initial_count", "minimum", "per_tile", "maximum", "expected"),
    [
        (0, 5, 3, 100, 5),
        (1, 10, 2, 100, 10),
        (4, 1, 3, 100, 12),
        (10, 1, 3, 20, 20),
    ],
)
def test_compute_request_budget_uses_exact_dynamic_formula(
    initial_count: int,
    minimum: int,
    per_tile: int,
    maximum: int,
    expected: int,
):
    assert compute_request_budget(initial_count, minimum, per_tile, maximum) == expected


@pytest.mark.parametrize(
    ("initial_count", "minimum", "per_tile", "maximum"),
    [
        (-1, 0, 1, 1),
        (0, -1, 1, 1),
        (0, 0, -1, 1),
        (0, 0, 1, 0),
        (0, 0, 1, -1),
    ],
)
def test_compute_request_budget_rejects_invalid_values(
    initial_count: int,
    minimum: int,
    per_tile: int,
    maximum: int,
):
    with pytest.raises(ValueError):
        compute_request_budget(initial_count, minimum, per_tile, maximum)


def test_initial_tiles_form_deterministic_row_major_grid_with_clipped_edges():
    planner = AoiTilePlanner(
        _aoi(box(0, 0, 250, 180)),
        request_crs="EPSG:5186",
        tile_size_m=100,
        max_split_depth=3,
        minimum_tile_size_m=25,
    )

    tiles = planner.initial_tiles()

    assert [tile.output_bounds for tile in tiles] == [
        (0.0, 0.0, 100.0, 100.0),
        (100.0, 0.0, 200.0, 100.0),
        (200.0, 0.0, 250.0, 100.0),
        (0.0, 100.0, 100.0, 180.0),
        (100.0, 100.0, 200.0, 180.0),
        (200.0, 100.0, 250.0, 180.0),
    ]
    assert [tile.request_bounds for tile in tiles] == [tile.output_bounds for tile in tiles]
    assert [tile.depth for tile in tiles] == [0] * 6


def test_planner_does_not_mutate_input_aoi():
    aoi = _aoi(box(0, 0, 100, 100))
    original_feature = aoi.features[0]
    original_wkb = original_feature.geometry.wkb
    original_properties = dict(original_feature.properties)

    planner = AoiTilePlanner(aoi, "EPSG:5186", 50, 2, 10)
    planner.initial_tiles()

    assert aoi.crs == "EPSG:5186"
    assert aoi.features == [original_feature]
    assert original_feature.geometry.wkb == original_wkb
    assert original_feature.properties == original_properties


@pytest.mark.parametrize(
    ("tile_size_m", "max_split_depth", "minimum_tile_size_m"),
    [
        (0, 1, 1),
        (-1, 1, 1),
        (1, -1, 1),
        (1, 1, 0),
        (1, 1, -1),
    ],
)
def test_planner_rejects_invalid_numeric_settings(
    tile_size_m: float,
    max_split_depth: int,
    minimum_tile_size_m: float,
):
    with pytest.raises(ValueError):
        AoiTilePlanner(
            _aoi(box(0, 0, 10, 10)),
            "EPSG:5186",
            tile_size_m,
            max_split_depth,
            minimum_tile_size_m,
        )


@pytest.mark.parametrize(
    ("tile_size_m", "minimum_tile_size_m"),
    [
        (float("nan"), 1.0),
        (float("inf"), 1.0),
        (float("-inf"), 1.0),
        (1.0, float("nan")),
        (1.0, float("inf")),
        (1.0, float("-inf")),
    ],
)
def test_planner_rejects_non_finite_dimensions(tile_size_m: float, minimum_tile_size_m: float):
    with pytest.raises(ValueError, match="finite"):
        AoiTilePlanner(
            _aoi(box(0, 0, 10, 10)),
            "EPSG:5186",
            tile_size_m,
            1,
            minimum_tile_size_m,
        )


@pytest.mark.parametrize(
    "aoi",
    [
        SpatialLayer("empty", "빈 범위", "EPSG:5186", "Polygon", "aoi"),
        _aoi(Polygon()),
    ],
)
def test_planner_rejects_aoi_without_non_empty_geometry(aoi: SpatialLayer):
    with pytest.raises(ValueError, match="non-empty"):
        AoiTilePlanner(aoi, "EPSG:5186", 100, 2, 25)


def test_planner_rejects_non_projected_output_crs():
    with pytest.raises(CrsError, match="투영좌표계"):
        AoiTilePlanner(_aoi(box(126, 36, 128, 38), "EPSG:4326"), "EPSG:4326", 100, 2, 25)


def test_planner_rejects_invalid_request_crs():
    with pytest.raises(CrsError):
        AoiTilePlanner(_aoi(box(0, 0, 10, 10)), "not-a-crs", 100, 2, 25)


def test_initial_tiles_filter_cell_wholly_inside_aoi_hole():
    geometry = Polygon(
        [(0, 0), (300, 0), (300, 300), (0, 300)],
        holes=[[(90, 90), (210, 90), (210, 210), (90, 210)]],
    )
    planner = AoiTilePlanner(_aoi(geometry), "EPSG:5186", 100, 2, 25)

    output_bounds = [tile.output_bounds for tile in planner.initial_tiles()]

    assert len(output_bounds) == 8
    assert (100.0, 100.0, 200.0, 200.0) not in output_bounds


def test_initial_tiles_filter_envelope_cells_outside_nonrectangular_aoi():
    geometry = box(0, 0, 80, 80).union(box(220, 220, 300, 300))
    planner = AoiTilePlanner(_aoi(geometry), "EPSG:5186", 100, 2, 25)

    assert [tile.output_bounds for tile in planner.initial_tiles()] == [
        (0.0, 0.0, 100.0, 100.0),
        (200.0, 200.0, 300.0, 300.0),
    ]


def test_initial_tiles_transform_whole_cell_bounds_to_request_crs():
    planner = AoiTilePlanner(
        _aoi(box(200000, 489000, 201000, 490000)),
        request_crs="EPSG:4326",
        tile_size_m=1000,
        max_split_depth=2,
        minimum_tile_size_m=50,
    )

    [tile] = planner.initial_tiles()

    assert tile.output_bounds == (200000.0, 489000.0, 201000.0, 490000.0)
    assert tile.request_bounds == pytest.approx(
        (127.0, 36.9998827268973, 127.01123579044965, 37.00889407585031),
        abs=1e-9,
    )


def test_initial_tile_request_bounds_enclose_nonlinear_edge_midpoint():
    output_bounds = (187500.0, 476500.0, 212500.0, 501500.0)
    planner = AoiTilePlanner(
        _aoi(box(*output_bounds)),
        request_crs="EPSG:4326",
        tile_size_m=25_000,
        max_split_depth=2,
        minimum_tile_size_m=50,
    )

    [tile] = planner.initial_tiles()
    edge_midpoint = transform_geometry(Point(200000.0, 501500.0), "EPSG:5186", "EPSG:4326")
    corner_only_bounds = transform_geometry(box(*output_bounds), "EPSG:5186", "EPSG:4326").bounds

    left, bottom, right, top = tile.request_bounds
    assert left <= edge_midpoint.x <= right
    assert bottom <= edge_midpoint.y <= top
    assert left <= corner_only_bounds[0]
    assert bottom <= corner_only_bounds[1]
    assert right >= corner_only_bounds[2]
    assert top >= corner_only_bounds[3]


@pytest.mark.parametrize(
    ("output_bounds", "expected_bounds"),
    [
        (
            (0.0, 0.0, 200.0, 100.0),
            [(0.0, 0.0, 100.0, 100.0), (100.0, 0.0, 200.0, 100.0)],
        ),
        (
            (0.0, 0.0, 100.0, 200.0),
            [(0.0, 0.0, 100.0, 100.0), (0.0, 100.0, 100.0, 200.0)],
        ),
        (
            (0.0, 0.0, 100.0, 100.0),
            [(0.0, 0.0, 50.0, 100.0), (50.0, 0.0, 100.0, 100.0)],
        ),
    ],
)
def test_split_bisects_longest_axis_with_x_tie_and_orders_children(
    output_bounds: tuple[float, float, float, float],
    expected_bounds: list[tuple[float, float, float, float]],
):
    planner = AoiTilePlanner(_aoi(box(-10, -10, 210, 210)), "EPSG:5186", 100, 10, 1)
    tile = AoiTile(output_bounds, output_bounds, depth=7)

    children = planner.split(tile)

    assert [child.output_bounds for child in children] == expected_bounds
    assert [child.request_bounds for child in children] == expected_bounds
    assert [child.depth for child in children] == [8, 8]


def test_split_filters_child_outside_actual_aoi():
    planner = AoiTilePlanner(_aoi(box(0, 0, 40, 100)), "EPSG:5186", 100, 4, 10)
    tile = AoiTile((0.0, 0.0, 100.0, 100.0), (0.0, 0.0, 100.0, 100.0))

    children = planner.split(tile)

    assert [child.output_bounds for child in children] == [(0.0, 0.0, 50.0, 100.0)]


def test_split_stops_at_depth_32_limit():
    planner = AoiTilePlanner(_aoi(box(0, 0, 100, 100)), "EPSG:5186", 100, 32, 1)
    tile = AoiTile((0.0, 0.0, 100.0, 100.0), (0.0, 0.0, 100.0, 100.0), depth=32)

    assert planner.split(tile) == []


def test_split_stops_before_creating_side_smaller_than_50_metres():
    planner = AoiTilePlanner(_aoi(box(0, 0, 100, 80)), "EPSG:5186", 100, 4, 50)
    too_small = AoiTile((0.0, 0.0, 50.0, 40.0), (0.0, 0.0, 50.0, 40.0))
    exact_minimum = AoiTile((0.0, 0.0, 100.0, 80.0), (0.0, 0.0, 100.0, 80.0))

    assert planner.split(too_small) == []
    assert [child.output_bounds for child in planner.split(exact_minimum)] == [
        (0.0, 0.0, 50.0, 80.0),
        (50.0, 0.0, 100.0, 80.0),
    ]


@pytest.mark.parametrize(
    "output_bounds",
    [
        pytest.param((0.0, 0.0, 200.0, 40.0), id="wide"),
        pytest.param((0.0, 0.0, 40.0, 200.0), id="tall"),
    ],
)
def test_split_stops_when_unchanged_side_is_smaller_than_minimum(
    output_bounds: tuple[float, float, float, float],
):
    planner = AoiTilePlanner(_aoi(box(*output_bounds)), "EPSG:5186", 200, 4, 50)
    tile = AoiTile(output_bounds, output_bounds)

    assert planner.split(tile) == []


def test_split_transforms_each_whole_child_polygon_to_request_crs():
    planner = AoiTilePlanner(
        _aoi(box(200000, 489000, 202000, 490000)),
        "EPSG:4326",
        2000,
        4,
        100,
    )
    tile = AoiTile((200000.0, 489000.0, 202000.0, 490000.0), (0.0, 0.0, 0.0, 0.0))

    children = planner.split(tile)

    assert [child.request_bounds for child in children] == pytest.approx(
        [
            transform_geometry(box(*child.output_bounds), "EPSG:5186", "EPSG:4326").bounds
            for child in children
        ]
    )
