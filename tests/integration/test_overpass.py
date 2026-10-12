from __future__ import annotations

import json
from collections.abc import Iterator
from copy import deepcopy
from pathlib import Path

import httpx
import pytest
from shapely.geometry import box

from routegis.gis import AoiTilePlanner, transform_geometry
from routegis.models import LayerDefinition, SpatialFeature, SpatialLayer
from routegis.providers.catalog import LayerCatalog
from routegis.providers.overpass.client import (
    DEFAULT_MAX_RESPONSE_BYTES,
    DEFAULT_OVERPASS_ENDPOINT,
    OverpassClient,
)


FIXTURE_DIRECTORY = Path(__file__).parents[1] / "fixtures"


def _definition(layer_id: str) -> LayerDefinition:
    return LayerCatalog().get(layer_id)


def _aoi(
    bounds: tuple[float, float, float, float] = (126.99, 37.49, 127.04, 37.54),
) -> SpatialLayer:
    geometry = transform_geometry(box(*bounds), "EPSG:4326", "EPSG:5186")
    return SpatialLayer(
        name="route_buffer",
        display_name="사업구간 주변 범위",
        crs="EPSG:5186",
        geometry_type="Polygon",
        category="route",
        features=[SpatialFeature(geometry)],
    )


def _output_aoi(
    bounds: tuple[float, float, float, float],
) -> SpatialLayer:
    return SpatialLayer(
        name="route_buffer",
        display_name="사업구간 주변 범위",
        crs="EPSG:5186",
        geometry_type="Polygon",
        category="route",
        features=[SpatialFeature(box(*bounds))],
    )


def _fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURE_DIRECTORY / name).read_text(encoding="utf-8"))


def _normalized_query(request: httpx.Request) -> str:
    return " ".join(request.content.decode("utf-8").split())


def _bbox_text(bounds: tuple[float, float, float, float]) -> str:
    west, south, east, north = bounds
    return ",".join(format(value, ".15g") for value in (south, west, north, east))


def _one_tile_bounds(aoi: SpatialLayer) -> tuple[float, float, float, float]:
    tiles = AoiTilePlanner(aoi, "EPSG:4326", 25_000, 32, 50).initial_tiles()
    assert len(tiles) == 1
    return tiles[0].request_bounds


def test_provider_package_exports_overpass_client():
    from routegis.providers.overpass import OverpassClient as ExportedOverpassClient

    assert ExportedOverpassClient is OverpassClient


@pytest.mark.parametrize(
    ("layer_id", "expected_template"),
    [
        (
            "power_line",
            '[out:json][timeout:180]; way["power"="line"]({bbox})->.lines; '
            '( .lines; node(w.lines)["power"~"^(tower|pole)$"]; ); out body geom;',
        ),
        (
            "power_support",
            '[out:json][timeout:180]; way["power"="line"]({bbox})->.lines; '
            '( .lines; node(w.lines)["power"~"^(tower|pole)$"]; ); out body geom;',
        ),
        (
            "river_centerline",
            '[out:json][timeout:180]; ( '
            'way["waterway"~"^(river|stream)$"]({bbox}); '
            'way["natural"="water"]["water"="river"]({bbox}); '
            'relation["natural"="water"]["water"="river"]({bbox}); '
            'way["waterway"="riverbank"]({bbox}); '
            'relation["waterway"="riverbank"]({bbox}); '
            '); out body geom;',
        ),
        (
            "river_area",
            '[out:json][timeout:180]; ( '
            'way["waterway"~"^(river|stream)$"]({bbox}); '
            'way["natural"="water"]["water"="river"]({bbox}); '
            'relation["natural"="water"]["water"="river"]({bbox}); '
            'way["waterway"="riverbank"]({bbox}); '
            'relation["waterway"="riverbank"]({bbox}); '
            '); out body geom;',
        ),
    ],
)
def test_exact_post_query_bbox_order_and_configurable_endpoint(
    layer_id: str,
    expected_template: str,
):
    requests: list[httpx.Request] = []
    endpoint = "https://overpass.example.test/custom/interpreter"
    aoi = _aoi()

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"version": 0.6, "elements": []})

    with httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    ) as http_client:
        result = OverpassClient(
            endpoint=endpoint,
            retries=0,
            client=http_client,
        ).fetch_layer(_definition(layer_id), aoi)

    assert result.status == "success"
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == endpoint
    assert request.headers["User-Agent"] == "RouteGIS-AutoBuilder/0.2.5"
    expected = expected_template.format(bbox=_bbox_text(_one_tile_bounds(aoi)))
    assert _normalized_query(request) == expected


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://overpass.example.test/api/interpreter",
        "ftp://overpass.example.test/api/interpreter",
        "//overpass.example.test/api/interpreter",
        "https://user:password@overpass.example.test/api/interpreter",
        "https://overpass.example.test/api/interpreter?token=secret",
        "https://overpass.example.test/api/interpreter#fragment",
    ],
)
def test_endpoint_must_be_an_https_url_without_credentials_or_suffix_data(endpoint: str):
    with pytest.raises(ValueError, match="endpoint"):
        OverpassClient(endpoint=endpoint)


def test_default_endpoint_timeout_and_redirect_policy(monkeypatch: pytest.MonkeyPatch):
    created: list[httpx.Client] = []
    real_client = httpx.Client

    def client_factory(**options: object) -> httpx.Client:
        assert options["follow_redirects"] is False
        timeout = options["timeout"]
        assert isinstance(timeout, httpx.Timeout)
        assert timeout.connect == 180.0
        assert timeout.read == 180.0
        client = real_client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json={"version": 0.6, "elements": []})
            )
        )
        created.append(client)
        return client

    monkeypatch.setattr("routegis.providers.overpass.client.httpx.Client", client_factory)

    with OverpassClient() as client:
        assert client.endpoint == DEFAULT_OVERPASS_ENDPOINT
        assert not created[0].is_closed

    assert created[0].is_closed


def test_response_limit_is_an_absolute_64_mib_cap():
    with pytest.raises(ValueError, match="max_response_bytes"):
        OverpassClient(max_response_bytes=DEFAULT_MAX_RESPONSE_BYTES + 1)


def test_hand_authored_fixtures_contain_no_request_urls_or_credentials():
    for name in ("overpass_power.json", "overpass_river.json"):
        text = (FIXTURE_DIRECTORY / name).read_text(encoding="utf-8")
        assert "http://" not in text
        assert "https://" not in text
        assert "token" not in text.casefold()
        assert "key" not in text.casefold()
        assert isinstance(_fixture(name)["elements"], list)


def _fetch_payload(
    layer_id: str,
    payload: dict[str, object],
    *,
    aoi: SpatialLayer | None = None,
    **client_options: object,
):
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        return OverpassClient(
            retries=0,
            client=http_client,
            **client_options,
        ).fetch_layer(_definition(layer_id), aoi or _aoi())


def _assert_atomic_failure(
    result: object,
    expected_bbox: tuple[float, float, float, float] | None = None,
) -> None:
    assert getattr(result, "status") == "failed"
    assert getattr(result, "feature_count") == 0
    assert getattr(result, "layer") is None
    assert getattr(result, "failed_tile_count") == 1
    if expected_bbox is not None:
        assert getattr(result, "failed_tile_bbox") == pytest.approx(expected_bbox)


def _positions(
    coordinates: list[tuple[float, float]],
) -> list[dict[str, float]]:
    return [{"lon": longitude, "lat": latitude} for longitude, latitude in coordinates]


def _member(
    reference: int,
    role: str,
    coordinates: list[tuple[float, float]] | None,
) -> dict[str, object]:
    member: dict[str, object] = {
        "type": "way",
        "ref": reference,
        "role": role,
    }
    if coordinates is not None:
        member["geometry"] = _positions(coordinates)
    return member


def _relation_payload(members: list[dict[str, object]]) -> dict[str, object]:
    return {
        "version": 0.6,
        "elements": [
            {
                "type": "relation",
                "id": 900,
                "members": members,
                "tags": {
                    "natural": "water",
                    "type": "multipolygon",
                    "water": "river",
                },
            }
        ],
    }


def test_power_fixture_keeps_only_transmission_line_and_its_tagged_members():
    payload = _fixture("overpass_power.json")

    line_result = _fetch_payload("power_line", payload)
    support_result = _fetch_payload("power_support", payload)

    assert line_result.status == "success"
    assert line_result.layer is not None
    assert line_result.layer.source == "osm"
    assert line_result.layer.crs == "EPSG:5186"
    assert line_result.layer.geometry_type == "MultiLineString"
    assert [feature.feature_id for feature in line_result.layer.features] == ["way/100"]
    assert all(
        feature.geometry.geom_type == "MultiLineString"
        for feature in line_result.layer.features
    )

    assert support_result.status == "success"
    assert support_result.layer is not None
    assert support_result.layer.source == "osm"
    assert support_result.layer.geometry_type == "MultiPoint"
    assert [feature.feature_id for feature in support_result.layer.features] == [
        "node/1",
        "node/2",
    ]
    assert all(
        feature.geometry.geom_type == "MultiPoint"
        for feature in support_result.layer.features
    )


def test_river_fixture_parses_centerlines_and_area_relations_with_holes():
    payload = _fixture("overpass_river.json")

    centerline_result = _fetch_payload("river_centerline", payload)
    area_result = _fetch_payload("river_area", payload)

    assert centerline_result.status == "success"
    assert centerline_result.layer is not None
    assert [feature.feature_id for feature in centerline_result.layer.features] == [
        "way/300",
        "way/301",
    ]
    assert all(
        feature.geometry.geom_type == "MultiLineString"
        for feature in centerline_result.layer.features
    )

    assert area_result.status == "success"
    assert area_result.layer is not None
    assert [feature.feature_id for feature in area_result.layer.features] == [
        "relation/500",
        "relation/600",
        "way/400",
        "way/401",
    ]
    relation = next(
        feature
        for feature in area_result.layer.features
        if feature.feature_id == "relation/500"
    )
    assert relation.geometry.geom_type == "MultiPolygon"
    assert len(relation.geometry.geoms) == 1
    assert len(relation.geometry.geoms[0].interiors) == 1
    assert relation.geometry.is_valid
    assert all(
        feature.feature_id not in {"way/501", "way/502", "way/601"}
        for feature in area_result.layer.features
    )


def test_intersecting_complete_geometry_is_transformed_but_never_clipped():
    aoi = _aoi((126.99, 37.50, 127.01, 37.51))
    payload = {
        "version": 0.6,
        "elements": [
            {
                "type": "way",
                "id": 777,
                "nodes": [1, 2],
                "geometry": [
                    {"lat": 37.505, "lon": 126.8},
                    {"lat": 37.505, "lon": 127.2},
                ],
                "tags": {"waterway": "river"},
            }
        ],
    }

    result = _fetch_payload("river_centerline", payload, aoi=aoi)

    assert result.status == "success"
    assert result.layer is not None
    assert result.feature_count == 1
    geometry_bounds = result.layer.features[0].geometry.bounds
    aoi_bounds = aoi.features[0].geometry.bounds
    assert geometry_bounds[0] < aoi_bounds[0]
    assert geometry_bounds[2] > aoi_bounds[2]


def test_complete_geometry_outside_aoi_is_filtered_after_transformation():
    payload = {
        "version": 0.6,
        "elements": [
            {
                "type": "way",
                "id": 778,
                "nodes": [1, 2],
                "geometry": [
                    {"lat": 36.0, "lon": 126.0},
                    {"lat": 36.1, "lon": 126.1},
                ],
                "tags": {"waterway": "stream"},
            }
        ],
    }

    result = _fetch_payload("river_centerline", payload)

    assert result.status == "success"
    assert result.layer is not None
    assert result.layer.features == []
    assert result.feature_count == 0


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            _relation_payload(
                [
                    _member(
                        1,
                        "outer",
                        [
                            (127.000, 37.500),
                            (127.020, 37.500),
                            (127.020, 37.520),
                            (127.000, 37.520),
                            (127.000, 37.500),
                        ],
                    ),
                    _member(2, "inner", None),
                ]
            ),
            "Geometry",
        ),
        (
            _relation_payload(
                [
                    _member(
                        1,
                        "outer",
                        [
                            (127.000, 37.500),
                            (127.020, 37.500),
                            (127.020, 37.520),
                            (127.000, 37.520),
                            (127.000, 37.500),
                        ],
                    ),
                    _member(
                        2,
                        "inner",
                        [
                            (127.030, 37.500),
                            (127.035, 37.500),
                            (127.035, 37.505),
                            (127.030, 37.505),
                            (127.030, 37.500),
                        ],
                    ),
                ]
            ),
            "inner",
        ),
        (
            _relation_payload(
                [
                    _member(
                        1,
                        "outer",
                        [(127.000, 37.500), (127.010, 37.500)],
                    ),
                    _member(
                        2,
                        "outer",
                        [(127.020, 37.520), (127.000, 37.520)],
                    ),
                ]
            ),
            "endpoint",
        ),
    ],
    ids=["missing-member-geometry", "uncontained-inner", "unjoinable-outer"],
)
def test_invalid_relation_geometry_fails_the_layer_atomically(
    payload: dict[str, object],
    message: str,
):
    result = _fetch_payload("river_area", payload)

    _assert_atomic_failure(result)
    assert message in (result.error or "")


def test_point_touching_inner_rings_use_a_valid_segment_decomposition():
    touch = (127.010, 37.510)
    payload = _relation_payload(
        [
            _member(
                100,
                "outer",
                [
                    (126.995, 37.495),
                    (127.025, 37.495),
                    (127.025, 37.525),
                    (126.995, 37.525),
                    (126.995, 37.495),
                ],
            ),
            _member(
                1,
                "inner",
                [
                    (127.000, 37.500),
                    (127.000, 37.510),
                    touch,
                ],
            ),
            _member(
                2,
                "inner",
                [
                    touch,
                    (127.020, 37.510),
                    (127.020, 37.520),
                ],
            ),
            _member(
                3,
                "inner",
                [
                    (127.020, 37.520),
                    (127.010, 37.520),
                    touch,
                ],
            ),
            _member(
                4,
                "inner",
                [
                    touch,
                    (127.010, 37.500),
                    (127.000, 37.500),
                ],
            ),
        ]
    )

    result = _fetch_payload("river_area", payload)

    assert result.status == "success"
    assert result.layer is not None
    assert result.feature_count == 1
    geometry = result.layer.features[0].geometry
    assert geometry.is_valid
    assert geometry.geom_type == "MultiPolygon"
    assert len(geometry.geoms) == 1
    assert len(geometry.geoms[0].interiors) == 2


def test_unclosed_way_ring_is_closed_and_repairable_bow_tie_is_preserved():
    payload = {
        "version": 0.6,
        "elements": [
            {
                "type": "way",
                "id": 910,
                "geometry": _positions(
                    [
                        (127.000, 37.500),
                        (127.020, 37.520),
                        (127.000, 37.520),
                        (127.020, 37.500),
                    ]
                ),
                "tags": {"natural": "water", "water": "river"},
            }
        ],
    }

    result = _fetch_payload("river_area", payload)

    assert result.status == "success"
    assert result.layer is not None
    assert result.feature_count == 1
    geometry = result.layer.features[0].geometry
    assert geometry.geom_type == "MultiPolygon"
    assert geometry.is_valid
    assert all(
        polygon.exterior.coords[0] == polygon.exterior.coords[-1]
        for polygon in geometry.geoms
    )


def test_unrepairable_degenerate_way_polygon_fails_atomically():
    payload = {
        "version": 0.6,
        "elements": [
            {
                "type": "way",
                "id": 911,
                "geometry": _positions(
                    [
                        (127.000, 37.500),
                        (127.010, 37.510),
                        (127.020, 37.520),
                    ]
                ),
                "tags": {"waterway": "riverbank"},
            }
        ],
    }

    result = _fetch_payload("river_area", payload)

    _assert_atomic_failure(result)
    assert "링" in (result.error or "") or "복구" in (result.error or "")


def test_content_fingerprint_deduplicates_normalized_geometry_and_tags_only():
    shared_tags = {"name": "Same river", "waterway": "river"}
    forward = [(126.995, 37.505), (127.005, 37.506), (127.015, 37.507)]
    payload = {
        "version": 0.6,
        "elements": [
            {
                "type": "way",
                "id": 10,
                "geometry": _positions(forward),
                "tags": shared_tags,
            },
            {
                "type": "way",
                "id": 11,
                "geometry": _positions(list(reversed(forward))),
                "tags": dict(reversed(list(shared_tags.items()))),
            },
            {
                "type": "way",
                "id": 12,
                "geometry": _positions(forward),
                "tags": {"name": "Different river", "waterway": "river"},
            },
        ],
    }

    result = _fetch_payload("river_centerline", payload)

    assert result.status == "success"
    assert result.layer is not None
    assert [feature.feature_id for feature in result.layer.features] == [
        "way/10",
        "way/12",
    ]


def test_same_stable_id_with_conflicting_content_reports_source_id_and_tile():
    aoi = _aoi()
    planner = AoiTilePlanner(aoi, "EPSG:4326", 2_500, 32, 50)
    tiles = planner.initial_tiles()
    assert len(tiles) > 1
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        longitude = 127.000 if attempts == 1 else 127.001
        return httpx.Response(
            200,
            json={
                "version": 0.6,
                "elements": [
                    {
                        "type": "way",
                        "id": 920,
                        "geometry": _positions(
                            [(longitude, 37.500), (longitude + 0.01, 37.510)]
                        ),
                        "tags": {"waterway": "river"},
                    }
                ],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = OverpassClient(
            retries=0,
            tile_size_m=2_500,
            client=http_client,
        ).fetch_layer(
            _definition("river_centerline"),
            aoi,
        )

    _assert_atomic_failure(result, tiles[1].output_bounds)
    assert attempts == 2
    assert result.request_count == 2
    assert result.successful_tile_count == 1
    assert "source=osm" in (result.error or "")
    assert "id=way/920" in (result.error or "")
    assert "tile=" in (result.error or "")


def _fetch_cross_tile_payloads(
    layer_id: str,
    first_payload: dict[str, object],
    changed_payload: dict[str, object],
):
    aoi = _aoi()
    tiles = AoiTilePlanner(aoi, "EPSG:4326", 2_500, 32, 50).initial_tiles()
    assert len(tiles) > 1
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        payload = first_payload if attempts == 1 else changed_payload
        return httpx.Response(200, json=payload)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = OverpassClient(
            retries=0,
            tile_size_m=2_500,
            client=http_client,
        ).fetch_layer(_definition(layer_id), aoi)

    return result, tiles, attempts


def _assert_second_tile_source_conflict(
    result: object,
    tiles: list[object],
    attempts: int,
    identity: str,
) -> None:
    second_bounds = getattr(tiles[1], "output_bounds")
    first_bounds = getattr(tiles[0], "output_bounds")
    _assert_atomic_failure(result, second_bounds)
    assert attempts == 2
    assert getattr(result, "request_count") == 2
    assert getattr(result, "successful_tile_count") == 1
    error = getattr(result, "error") or ""
    assert "source=osm" in error
    assert f"id={identity}" in error
    assert f"tile={second_bounds}" in error
    assert f"previous_tile={first_bounds}" in error


def test_same_relation_id_with_changed_member_ref_fails_before_suppression():
    ring = [
        (127.000, 37.500),
        (127.010, 37.500),
        (127.010, 37.510),
        (127.000, 37.510),
        (127.000, 37.500),
    ]

    def payload(reference: int) -> dict[str, object]:
        return {
            "version": 0.6,
            "elements": [
                {
                    "type": "relation",
                    "id": 950,
                    "members": [_member(reference, "outer", ring)],
                    "tags": {
                        "natural": "water",
                        "type": "multipolygon",
                        "water": "river",
                    },
                }
            ],
        }

    result, tiles, attempts = _fetch_cross_tile_payloads(
        "river_area",
        payload(951),
        payload(952),
    )

    _assert_second_tile_source_conflict(result, tiles, attempts, "relation/950")


def test_same_way_id_with_predicate_changing_tags_cannot_evade_conflict():
    def payload(waterway: str) -> dict[str, object]:
        return {
            "version": 0.6,
            "elements": [
                {
                    "type": "way",
                    "id": 960,
                    "nodes": [9601, 9602],
                    "geometry": _positions(
                        [(126.995, 37.505), (127.015, 37.507)]
                    ),
                    "tags": {"waterway": waterway},
                }
            ],
        }

    result, tiles, attempts = _fetch_cross_tile_payloads(
        "river_centerline",
        payload("river"),
        payload("canal"),
    )

    _assert_second_tile_source_conflict(result, tiles, attempts, "way/960")


def test_same_way_id_with_changed_node_membership_is_a_source_conflict():
    def payload(nodes: list[int]) -> dict[str, object]:
        return {
            "version": 0.6,
            "elements": [
                {
                    "type": "way",
                    "id": 970,
                    "nodes": nodes,
                    "geometry": _positions(
                        [(126.995, 37.505), (127.015, 37.507)]
                    ),
                    "tags": {"waterway": "river"},
                }
            ],
        }

    result, tiles, attempts = _fetch_cross_tile_payloads(
        "river_centerline",
        payload([9701, 9702]),
        payload([9701, 9703]),
    )

    _assert_second_tile_source_conflict(result, tiles, attempts, "way/970")


def test_nonfinite_source_structure_is_rejected_before_layer_filtering():
    content = (
        b'{"version":0.6,"elements":['
        b'{"type":"way","id":980,"nodes":[9801,9802],'
        b'"geometry":[{"lat":37.505,"lon":126.995},'
        b'{"lat":37.507,"lon":127.015}],'
        b'"tags":{"waterway":"canal"},"metadata":NaN}'
        b"]}"
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=content)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = OverpassClient(retries=0, client=http_client).fetch_layer(
            _definition("river_centerline"),
            _aoi(),
        )

    _assert_atomic_failure(result)
    assert result.request_count == 1
    assert "source" in (result.error or "").casefold()


def test_excessive_source_nesting_is_an_atomic_provider_failure():
    metadata: object = "leaf"
    for _ in range(200):
        metadata = [metadata]
    payload = {
        "version": 0.6,
        "elements": [
            {
                "type": "way",
                "id": 981,
                "nodes": [9811, 9812],
                "geometry": _positions(
                    [(126.995, 37.505), (127.015, 37.507)]
                ),
                "tags": {"waterway": "canal"},
                "metadata": metadata,
            }
        ],
    }

    result = _fetch_payload("river_centerline", payload)

    _assert_atomic_failure(result)
    assert result.request_count == 1
    assert "depth" in (result.error or "").casefold()


def test_json_decoder_recursion_is_converted_to_atomic_provider_failure():
    content = (
        b'{"version":0.6,"elements":[{"type":"way","id":982,'
        b'"nodes":[9821,9822],"geometry":['
        b'{"lat":37.505,"lon":126.995},{"lat":37.507,"lon":127.015}],'
        b'"tags":{"waterway":"canal"},"metadata":'
        + b"[" * 2_000
        + b"0"
        + b"]" * 2_000
        + b"}]}"
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=content)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = OverpassClient(retries=0, client=http_client).fetch_layer(
            _definition("river_centerline"),
            _aoi(),
        )

    _assert_atomic_failure(result)
    assert result.request_count == 1
    assert "JSON" in (result.error or "") or "depth" in (result.error or "").casefold()


def test_source_item_bound_is_an_atomic_provider_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "routegis.providers.overpass.client.MAX_SOURCE_CANONICAL_ITEMS",
        20,
        raising=False,
    )
    payload = {
        "version": 0.6,
        "elements": [
            {
                "type": "way",
                "id": 983,
                "nodes": [9831, 9832],
                "geometry": _positions(
                    [(126.995, 37.505), (127.015, 37.507)]
                ),
                "tags": {"waterway": "canal"},
                "metadata": list(range(30)),
            }
        ],
    }

    result = _fetch_payload("river_centerline", payload)

    _assert_atomic_failure(result)
    assert "item" in (result.error or "").casefold()


def test_cyclic_source_structure_is_an_atomic_provider_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    cycle: list[object] = []
    cycle.append(cycle)
    payload = {
        "version": 0.6,
        "elements": [
            {
                "type": "way",
                "id": 984,
                "nodes": [9841, 9842],
                "geometry": _positions(
                    [(126.995, 37.505), (127.015, 37.507)]
                ),
                "tags": {"waterway": "canal"},
                "metadata": cycle,
            }
        ],
    }
    monkeypatch.setattr(
        OverpassClient,
        "_payload",
        staticmethod(lambda _: payload),
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"{}")

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = OverpassClient(retries=0, client=http_client).fetch_layer(
            _definition("river_centerline"),
            _aoi(),
        )

    _assert_atomic_failure(result)
    assert "순환" in (result.error or "")


def test_relation_member_way_is_suppressed_even_when_seen_in_an_earlier_tile():
    aoi = _aoi()
    member_way = {
        "type": "way",
        "id": 930,
        "geometry": _positions(
            [
                (127.000, 37.500),
                (127.010, 37.500),
                (127.010, 37.510),
                (127.000, 37.510),
                (127.000, 37.500),
            ]
        ),
        "tags": {"natural": "water", "water": "river"},
    }
    relation = {
        "type": "relation",
        "id": 931,
        "members": [
            _member(
                930,
                "outer",
                [
                    (127.000, 37.500),
                    (127.010, 37.500),
                    (127.010, 37.510),
                    (127.000, 37.510),
                    (127.000, 37.500),
                ],
            )
        ],
        "tags": {"natural": "water", "type": "multipolygon", "water": "river"},
    }
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        elements = [member_way] if attempts == 1 else [relation]
        return httpx.Response(200, json={"version": 0.6, "elements": elements})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = OverpassClient(
            retries=0,
            tile_size_m=2_500,
            client=http_client,
        ).fetch_layer(
            _definition("river_area"),
            aoi,
        )

    assert result.status == "success"
    assert result.layer is not None
    assert [feature.feature_id for feature in result.layer.features] == ["relation/931"]


@pytest.mark.parametrize("status_code", [429, 502, 503, 504])
def test_only_approved_transient_statuses_retry_to_three_attempts(status_code: int):
    attempts = 0
    sleeps: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(status_code)
        return httpx.Response(200, json={"version": 0.6, "elements": []})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = OverpassClient(
            retries=2,
            backoff=0.25,
            client=http_client,
            sleep=sleeps.append,
        ).fetch_layer(_definition("power_line"), _aoi())

    assert result.status == "success"
    assert attempts == 3
    assert result.request_count == 3
    assert result.successful_tile_count == 1
    assert sleeps == [0.25, 0.5]


@pytest.mark.parametrize("status_code", [400, 401, 404, 500, 501])
def test_non_transient_http_status_is_terminal(status_code: int):
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(status_code)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = OverpassClient(retries=2, client=http_client).fetch_layer(
            _definition("power_line"),
            _aoi(),
        )

    _assert_atomic_failure(result)
    assert attempts == 1
    assert result.request_count == 1
    assert f"HTTP {status_code}" in (result.error or "")


def test_timeout_and_transport_errors_use_the_same_bounded_retry_policy():
    attempts = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadTimeout("temporary timeout", request=request)
        if attempts == 2:
            raise httpx.ConnectError("temporary transport error", request=request)
        return httpx.Response(200, json={"version": 0.6, "elements": []})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = OverpassClient(
            retries=2,
            backoff=0.1,
            client=http_client,
            sleep=sleeps.append,
        ).fetch_layer(_definition("power_line"), _aoi())

    assert result.status == "success"
    assert attempts == 3
    assert result.request_count == 3
    assert sleeps == [0.1, 0.2]


def test_terminal_transport_error_exposes_only_safe_exception_class():
    secret = "Authorization: Bearer super-secret"

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(secret, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = OverpassClient(retries=0, client=http_client).fetch_layer(
            _definition("power_line"),
            _aoi(),
        )

    _assert_atomic_failure(result)
    assert "Overpass 요청에 실패했습니다: ConnectError" in (result.error or "")
    assert secret not in (result.error or "")
    assert "Bearer" not in (result.error or "")


@pytest.mark.parametrize(("exception", "message", "expected"), [
    (httpx.ConnectError, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed", "인증서"),
    (httpx.ConnectError, "[Errno 11001] getaddrinfo failed", "DNS"),
    (httpx.ReadTimeout, "", "시간 초과"),
    (httpx.ProxyError, "proxy authentication failed", "프록시"),
])
def test_transport_failures_explain_network_cause_without_exposing_raw_data(exception, message, expected):
    def handler(request):
        raise exception(message + " private-token", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = OverpassClient(retries=0, client=http_client).fetch_layer(_definition("power_line"), _aoi())

    assert result.status == "failed"
    assert expected in (result.error or "")
    assert "private-token" not in (result.error or "")


def test_transient_response_is_closed_before_backoff_sleep():
    attempts = 0
    responses: list[httpx.Response] = []
    sleeps: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            response = httpx.Response(503, stream=_ChunkedStream())
            responses.append(response)
            return response
        return httpx.Response(200, json={"version": 0.6, "elements": []})

    def sleep(delay: float) -> None:
        assert responses[-1].is_closed
        sleeps.append(delay)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = OverpassClient(
            retries=1,
            backoff=0.25,
            client=http_client,
            sleep=sleep,
        ).fetch_layer(_definition("power_line"), _aoi())

    assert result.status == "success"
    assert attempts == 2
    assert responses[0].is_closed
    assert sleeps == [0.25]


def test_retry_configuration_cannot_exceed_three_real_attempts():
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = OverpassClient(retries=99, backoff=0, client=http_client).fetch_layer(
            _definition("power_line"),
            _aoi(),
        )

    _assert_atomic_failure(result)
    assert attempts == 3
    assert result.request_count == 3


def test_redirect_is_refused_even_when_injected_client_follows_redirects():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            302,
            headers={"Location": "https://redirected.example.test/interpreter"},
        )

    with httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    ) as http_client:
        result = OverpassClient(retries=2, client=http_client).fetch_layer(
            _definition("power_line"),
            _aoi(),
        )

    _assert_atomic_failure(result)
    assert len(requests) == 1
    assert result.request_count == 1
    assert "리디렉션" in (result.error or "")


class _ChunkedStream(httpx.SyncByteStream):
    def __iter__(self) -> Iterator[bytes]:
        yield b'{"version":0.6,'
        yield b'"elements":[]}'


@pytest.mark.parametrize("declared", [True, False])
def test_response_size_cap_applies_to_declared_and_streamed_bytes(declared: bool):
    def handler(_: httpx.Request) -> httpx.Response:
        if declared:
            return httpx.Response(
                200,
                headers={"content-length": "100"},
                content=b"{}",
            )
        return httpx.Response(200, stream=_ChunkedStream())

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = OverpassClient(
            retries=0,
            max_response_bytes=16,
            client=http_client,
        ).fetch_layer(_definition("power_line"), _aoi())

    _assert_atomic_failure(result)
    assert result.request_count == 1
    assert "크기 제한" in (result.error or "")


def test_each_retry_consumes_budget_before_a_real_transport_attempt():
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = OverpassClient(
            retries=2,
            backoff=0,
            request_budget_minimum=2,
            request_budget_per_tile=0,
            request_budget_maximum=2,
            client=http_client,
        ).fetch_layer(_definition("power_line"), _aoi())

    _assert_atomic_failure(result)
    assert attempts == 2
    assert result.request_count == 2
    assert "budget=2" in (result.error or "")


def test_default_25_km_tiles_are_requested_sequentially_with_atomic_stats():
    aoi = _output_aoi((180_000.0, 480_000.0, 235_000.0, 490_000.0))
    tiles = AoiTilePlanner(aoi, "EPSG:4326", 25_000, 32, 50).initial_tiles()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"version": 0.6, "elements": []})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = OverpassClient(retries=0, client=http_client).fetch_layer(
            _definition("power_line"),
            aoi,
        )

    assert len(tiles) == 3
    assert len(requests) == len(tiles)
    for request, tile in zip(requests, tiles):
        assert f"({_bbox_text(tile.request_bounds)})" in _normalized_query(request)
    assert result.status == "success"
    assert result.layer is not None
    assert result.layer.features == []
    assert result.initial_tile_count == 3
    assert result.request_count == 3
    assert result.successful_tile_count == 3
    assert result.failed_tile_count == 0
    assert result.failed_tile_bbox is None


def test_first_terminal_tile_failure_discards_partial_layer_and_stops():
    aoi = _output_aoi((180_000.0, 480_000.0, 235_000.0, 490_000.0))
    tiles = AoiTilePlanner(aoi, "EPSG:4326", 25_000, 32, 50).initial_tiles()
    attempts = 0
    first_payload = {
        "version": 0.6,
        "elements": [
            {
                "type": "way",
                "id": 940,
                "geometry": _positions(
                    [(126.995, 37.505), (127.005, 37.506)]
                ),
                "tags": {"waterway": "river"},
            }
        ],
    }

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(200, json=first_payload)
        return httpx.Response(500)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = OverpassClient(retries=2, client=http_client).fetch_layer(
            _definition("river_centerline"),
            aoi,
        )

    _assert_atomic_failure(result, tiles[1].output_bounds)
    assert attempts == 2
    assert result.initial_tile_count == 3
    assert result.request_count == 2
    assert result.successful_tile_count == 1


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"not-json"),
        httpx.Response(200, json={"remark": "runtime error", "elements": []}),
        httpx.Response(200, json={"version": 0.6}),
    ],
    ids=["invalid-json", "service-remark", "missing-elements"],
)
def test_invalid_or_service_error_payload_is_atomic(response: httpx.Response):
    def handler(_: httpx.Request) -> httpx.Response:
        return deepcopy(response)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = OverpassClient(retries=0, client=http_client).fetch_layer(
            _definition("power_line"),
            _aoi(),
        )

    _assert_atomic_failure(result)
    assert result.request_count == 1


def test_catalog_remains_the_only_owner_of_osm_odbl_attribution():
    for layer_id in (
        "power_line",
        "power_support",
        "river_centerline",
        "river_area",
    ):
        definition = _definition(layer_id)
        assert definition.provider == "osm"
        assert definition.publisher == "OpenStreetMap contributors"
        assert definition.dataset_id == "openstreetmap"
        assert definition.license_name == "Open Database License (ODbL) 1.0"
        assert definition.license_url == "https://www.openstreetmap.org/copyright"

    result = _fetch_payload(
        "power_line",
        {"version": 0.6, "elements": []},
    )
    assert "license_name" not in vars(result)
    assert "license_url" not in vars(result)
