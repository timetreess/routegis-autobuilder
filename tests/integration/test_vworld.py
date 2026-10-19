from dataclasses import replace

import httpx
import pytest
from shapely.geometry import box

from routegis.errors import ProviderError
from routegis.models import LayerDefinition, SpatialFeature, SpatialLayer
from routegis.providers.vworld.client import VWorldClient


DEFINITION = LayerDefinition(
    id="cadastral",
    name="연속지적도",
    provider="vworld",
    service_type="wfs",
    endpoint="https://api.vworld.kr/req/wfs",
    data_id="lp_pa_cbnd_bubun",
    geometry_type="MultiPolygon",
    category="cadastral",
    request_crs="EPSG:4326",
    publisher="국토교통부 공간정보 오픈플랫폼(VWorld)",
    dataset_id="lp_pa_cbnd_bubun",
    license_name="VWorld OpenAPI 이용조건",
    license_url="https://www.vworld.kr/dev/v4dv_wmsguide2_s001.do",
)

DATA_DEFINITION = LayerDefinition(
    id="administrative_sido",
    name="시도 행정경계",
    provider="vworld",
    service_type="data",
    endpoint="https://api.vworld.kr/req/data",
    data_id="LT_C_ADSIDO_INFO",
    geometry_type="MultiPolygon",
    category="administrative",
    request_crs="EPSG:4326",
    publisher="국토교통부 공간정보 오픈플랫폼(VWorld)",
    dataset_id="LT_C_ADSIDO_INFO",
    license_name="VWorld OpenAPI 이용조건",
    license_url="https://www.vworld.kr/dev/v4dv_wmsguide2_s001.do",
    response_crs="EPSG:4326",
)

LIVE_DEFINITION = LayerDefinition(
    id="lt_l_gimsfault",
    name="단층",
    provider="vworld",
    service_type="wfs",
    endpoint="https://api.vworld.kr/req/wfs",
    data_id="lt_l_gimsfault",
    geometry_type="Unknown",
    category="facility",
    request_crs="EPSG:4326",
    publisher="국토교통부 공간정보 오픈플랫폼(VWorld)",
    dataset_id="lt_l_gimsfault",
    license_name="VWorld OpenAPI 이용조건",
    license_url="https://www.vworld.kr/dev/v4dv_wmsguide2_s001.do",
    response_crs="EPSG:4326",
)

LAND_OWNERSHIP_DEFINITION = LayerDefinition(
    id="land_ownership",
    name="토지소유공간정보",
    provider="vworld",
    service_type="wfs",
    endpoint="https://api.vworld.kr/req/wfs",
    data_id="dt_d160",
    geometry_type="MultiPolygon",
    category="land",
    request_crs="EPSG:4326",
    publisher="국토교통부 공간정보 오픈플랫폼(VWorld)",
    dataset_id="dt_d160",
    license_name="VWorld OpenAPI 이용조건",
    license_url="https://www.vworld.kr/dev/v4dv_wmsguide2_s001.do",
    response_crs="EPSG:4326",
)


def _aoi(bounds=(198_000.0, 486_000.0, 202_500.0, 492_000.0)):
    return SpatialLayer(
        "route_buffer",
        "사업구간 주변 범위",
        "EPSG:5186",
        "Polygon",
        "route",
        [SpatialFeature(box(*bounds))],
    )


def _empty_collection(_: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"type": "FeatureCollection", "features": []})


def test_catalog_authentication_error_preserves_service_error_code():
    content = b"""<ServiceExceptionReport><ServiceException code="INVALID_KEY">
      Unregistered authentication key
    </ServiceException></ServiceExceptionReport>"""
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=content))) as http_client:
        with pytest.raises(ProviderError, match="INVALID_KEY"):
            VWorldClient("placeholder", client=http_client).discover_layers()


def test_data_authentication_error_preserves_code_even_without_message():
    payload = {"response": {"status": "ERROR", "error": {"code": "INVALID_KEY"}}}
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as http_client:
        result = VWorldClient("placeholder", client=http_client).fetch_layer(DATA_DEFINITION, _aoi())

    assert result.status == "failed"
    assert "INVALID_KEY" in (result.error or "")


def _polygon_feature(
    identifier: str,
    bounds: tuple[float, float, float, float],
    **properties,
) -> dict:
    min_x, min_y, max_x, max_y = bounds
    return {
        "type": "Feature",
        "id": identifier,
        "properties": properties,
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [min_x, min_y],
                    [max_x, min_y],
                    [max_x, max_y],
                    [min_x, max_y],
                    [min_x, min_y],
                ]
            ],
        },
    }


@pytest.mark.parametrize(
    "kwargs",
    [
        {"land_ownership_tile_size_m": 0},
        {"default_tile_size_m": 0},
        {"request_budget_minimum": -1},
        {"request_budget_per_tile": -1},
        {"request_budget_maximum": 0},
        {"max_split_depth": -1},
        {"minimum_tile_size_m": 0},
        {"max_response_bytes": 0},
        {"max_response_bytes": 64 * 1024 * 1024 + 1},
    ],
)
def test_constructor_rejects_invalid_planner_and_budget_boundaries(kwargs):
    with pytest.raises(ValueError):
        VWorldClient("secret-key", **kwargs)


@pytest.mark.parametrize(
    ("definition", "expected_tile_count"),
    [
        (replace(LAND_OWNERSHIP_DEFINITION, data_id="different"), 2),
        (replace(DEFINITION, id="renamed", data_id="Dt_D160"), 2),
        (DEFINITION, 1),
    ],
)
def test_fetch_layer_selects_five_or_ten_kilometre_tiles(definition, expected_tile_count):
    definition = replace(definition, request_crs="EPSG:5186", response_crs="EPSG:5186")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _empty_collection(request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = VWorldClient("secret-key", retries=0, client=http_client).fetch_layer(
            definition,
            _aoi((0, 0, 9000, 1000)),
        )

    assert result.status == "success"
    assert result.initial_tile_count == expected_tile_count
    assert result.request_count == expected_tile_count
    assert result.successful_tile_count == expected_tile_count
    assert len(requests) == expected_tile_count


def test_land_ownership_long_route_uses_more_than_one_hundred_initial_tiles_and_dynamic_budget():
    definition = replace(
        LAND_OWNERSHIP_DEFINITION,
        request_crs="EPSG:5186",
        response_crs="EPSG:5186",
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.params["maxFeatures"] == "1000"
        return _empty_collection(request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = VWorldClient("secret-key", retries=0, client=http_client).fetch_layer(
            definition,
            _aoi((0, 0, 505_000, 1000)),
        )

    assert result.status == "success"
    assert result.initial_tile_count == 101
    assert result.request_count == 101
    assert result.successful_tile_count == 101
    assert len(requests) == 101


def test_discover_layers_parses_namespaced_and_plain_feature_types(fixture_directory):
    capabilities = (fixture_directory / "vworld_capabilities.xml").read_bytes()

    def handler(request: httpx.Request):
        assert request.url.params["request"] == "GetCapabilities"
        assert request.url.params["version"] == "1.1.0"
        assert request.url.params["key"] == "secret-key"
        assert request.url.params["domain"] == "https://registered.example.com"
        return httpx.Response(200, content=capabilities)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        layers = VWorldClient(
            "secret-key",
            domain="https://registered.example.com",
            retries=0,
            client=http_client,
        ).discover_layers()

    by_id = {layer.id: layer for layer in layers}
    assert set(by_id) == {"lt_c_ud801", "lt_l_gimsfault", "pmap_od_zone"}
    assert by_id["lt_c_ud801"].name == "개발제한구역"
    assert by_id["lt_l_gimsfault"].name == "단층"
    assert by_id["pmap_od_zone"].name == "pmap_od_zone"
    assert all(layer.id == layer.data_id for layer in layers)
    assert all(layer.provider == "vworld" for layer in layers)
    assert all(layer.service_type == "wfs" for layer in layers)
    assert all(layer.endpoint == "https://api.vworld.kr/req/wfs" for layer in layers)
    assert all(layer.geometry_type == "Unknown" for layer in layers)
    assert all(layer.category == "facility" for layer in layers)
    assert all(layer.request_crs == "EPSG:4326" for layer in layers)
    assert all(layer.response_crs == "EPSG:4326" for layer in layers)
    assert all(layer.publisher == "국토교통부 공간정보 오픈플랫폼(VWorld)" for layer in layers)
    assert all(layer.dataset_id == layer.data_id for layer in layers)
    assert all(layer.license_name == "VWorld OpenAPI 이용조건" for layer in layers)
    assert all(
        layer.license_url == "https://www.vworld.kr/dev/v4dv_wmsguide2_s001.do"
        for layer in layers
    )


@pytest.mark.parametrize(("content", "message"), [
    (b"<WFS_Capabilities/>", "레이어 목록을 찾을 수 없습니다"),
    (b"<ServiceExceptionReport><ServiceException>Invalid key</ServiceException></ServiceExceptionReport>", "Invalid key"),
])
def test_discover_layers_rejects_response_without_feature_types(content, message):
    def handler(_request: httpx.Request):
        return httpx.Response(200, content=content)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = VWorldClient("bad-key", retries=0, client=http_client)
        with pytest.raises(ProviderError, match=message):
            client.discover_layers()


@pytest.mark.parametrize(
    ("definition", "expected_path"),
    [
        (DEFINITION, "/req/wfs"),
        (DATA_DEFINITION, "/req/data"),
    ],
)
def test_definition_endpoint_cannot_redirect_vworld_credentials(
    definition: LayerDefinition,
    expected_path: str,
):
    requests: list[httpx.Request] = []
    definition = replace(
        definition,
        endpoint="https://attacker.example/collect",
        request_crs="EPSG:5186",
        response_crs="EPSG:5186",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _empty_collection(request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = VWorldClient(
            "secret-key",
            retries=0,
            client=http_client,
        ).fetch_layer(definition, _aoi((0, 0, 100, 100)))

    assert result.status == "success"
    assert len(requests) == 1
    assert requests[0].url.host == "api.vworld.kr"
    assert requests[0].url.path == expected_path
    assert requests[0].url.params["key"] == "secret-key"


@pytest.mark.parametrize(
    ("gml_type", "expected"),
    [
        ("gml:PolygonPropertyType", "MultiPolygon"),
        ("gml:MultiSurfacePropertyType", "MultiPolygon"),
        ("gml:LineStringPropertyType", "MultiLineString"),
        ("gml:CurvePropertyType", "MultiLineString"),
        ("gml:PointPropertyType", "MultiPoint"),
        ("gml:MultiPointPropertyType", "MultiPoint"),
    ],
)
def test_describe_feature_type_maps_gml_geometry(gml_type, expected):
    schema = f"""<?xml version="1.0" encoding="UTF-8"?>
    <xsd:schema xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:gml="http://www.opengis.net/gml">
      <xsd:complexType name="sampleType">
        <xsd:sequence><xsd:element name="geom" type="{gml_type}" /></xsd:sequence>
      </xsd:complexType>
    </xsd:schema>""".encode()

    def handler(request: httpx.Request):
        assert request.url.params["request"] == "DescribeFeatureType"
        assert request.url.params["typeName"] == LIVE_DEFINITION.data_id
        return httpx.Response(200, content=schema)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        geometry_type = VWorldClient("secret-key", retries=0, client=http_client).describe_feature_type(
            LIVE_DEFINITION
        )

    assert geometry_type == expected


def test_unknown_geometry_is_resolved_before_get_feature():
    requests: list[str] = []

    def handler(request: httpx.Request):
        operation = request.url.params["request"]
        requests.append(operation)
        if operation == "DescribeFeatureType":
            return httpx.Response(
                200,
                content=b'<schema><element name="geom" type="gml:MultiCurvePropertyType" /></schema>',
            )
        return httpx.Response(
            200,
            json={
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "id": "fault-1",
                        "properties": {},
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [[127.0, 37.0], [127.01, 37.01]],
                        },
                    }
                ],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = VWorldClient("secret-key", retries=0, client=http_client).fetch_layer(LIVE_DEFINITION, _aoi())

    assert requests == ["DescribeFeatureType", "GetFeature"]
    assert result.status == "success"
    assert result.layer is not None
    assert result.layer.geometry_type == "MultiLineString"
    assert LIVE_DEFINITION.geometry_type == "Unknown"
    assert result.initial_tile_count == 1
    assert result.request_count == 2
    assert result.successful_tile_count == 1


def test_unsupported_describe_type_is_isolated_as_failed_download():
    def handler(_: httpx.Request):
        return httpx.Response(200, content=b'<schema><element name="geom" type="gml:GeometryPropertyType" /></schema>')

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = VWorldClient("secret-key", retries=0, client=http_client).fetch_layer(LIVE_DEFINITION, _aoi())

    assert result.status == "failed"
    assert result.layer is None
    assert "Geometry" in (result.error or "")
    assert result.initial_tile_count == 1
    assert result.request_count == 1
    assert result.successful_tile_count == 0
    assert result.failed_tile_count == 1
    assert result.failed_tile_bbox is None


def test_wfs_saturated_parent_is_discarded_and_replaced_by_successful_children():
    definition = replace(DEFINITION, request_crs="EPSG:5186", response_crs="EPSG:5186")
    requests: list[tuple[float, float, float, float]] = []

    def handler(request: httpx.Request):
        assert request.url.params.get("startIndex") in (None, "0")
        bounds = tuple(float(value) for value in request.url.params["bbox"].split(","))
        requests.append(bounds)
        if len(requests) == 1:
            return httpx.Response(
                200,
                json={
                    "type": "FeatureCollection",
                    "features": [
                        _polygon_feature("parent-a", (10, 10, 20, 20), source="parent"),
                        _polygon_feature("parent-b", (70, 10, 80, 20), source="parent"),
                    ],
                },
            )
        child = "left" if len(requests) == 2 else "right"
        child_bounds = (10, 10, 20, 20) if child == "left" else (70, 10, 80, 20)
        return httpx.Response(
            200,
            json={
                "type": "FeatureCollection",
                "features": [_polygon_feature(child, child_bounds, source=child)],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = VWorldClient(
            "secret-key",
            page_size=2,
            default_tile_size_m=100,
            minimum_tile_size_m=10,
            retries=0,
            client=http_client,
        ).fetch_layer(definition, _aoi((0, 0, 100, 40)))

    assert result.status == "success"
    assert result.feature_count == 2
    assert result.layer is not None
    assert {feature.properties["source"] for feature in result.layer.features} == {"left", "right"}
    assert requests == [(0.0, 0.0, 100.0, 40.0), (0.0, 0.0, 50.0, 40.0), (50.0, 0.0, 100.0, 40.0)]
    assert result.initial_tile_count == 1
    assert result.request_count == 3
    assert result.successful_tile_count == 2
    assert result.failed_tile_count == 0
    assert result.failed_tile_bbox is None


def test_wfs_reported_total_also_replaces_the_saturated_parent():
    definition = replace(DEFINITION, request_crs="EPSG:5186", response_crs="EPSG:5186")
    attempts = 0

    def handler(request: httpx.Request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(
                200,
                json={
                    "type": "FeatureCollection",
                    "totalFeatures": 2,
                    "features": [_polygon_feature("parent", (10, 10, 20, 20), source="parent")],
                },
            )
        return _empty_collection(request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = VWorldClient(
            "secret-key",
            default_tile_size_m=100,
            minimum_tile_size_m=10,
            retries=0,
            client=http_client,
        ).fetch_layer(definition, _aoi((0, 0, 100, 40)))

    assert result.status == "success"
    assert result.feature_count == 0
    assert result.request_count == 3
    assert result.successful_tile_count == 2


@pytest.mark.parametrize("maximum,expected_status", [(5000, "success"), (110, "failed")])
def test_dense_wfs_uses_reported_density_and_extends_estimate_but_keeps_hard_cap(maximum, expected_status):
    definition = replace(DEFINITION, request_crs="EPSG:5186", response_crs="EPSG:5186")
    features = [
        _polygon_feature(f"parcel-{x}-{y}", (x * 100 + 40, y * 100 + 40, x * 100 + 60, y * 100 + 60))
        for x in range(16) for y in range(16)
    ]
    calls, events = [], []

    def handler(request):
        bounds = tuple(map(float, request.url.params["bbox"].split(",")))
        calls.append(bounds)
        left, bottom, right, top = bounds
        matches = [f for f in features
                   if left < f["geometry"]["coordinates"][0][0][0] < right
                   and bottom < f["geometry"]["coordinates"][0][0][1] < top]
        return httpx.Response(200, json={"type": "FeatureCollection",
                              "numberMatched": len(matches), "features": matches[:4]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = VWorldClient("secret-key", page_size=4, retries=0,
                              request_budget_maximum=maximum, client=http_client,
                              on_progress=events.append).fetch_layer(definition, _aoi((0, 0, 1600, 1600)))

    assert result.status == expected_status, result.error
    assert result.request_count == len(calls)
    assert result.initial_tile_count == 1
    if expected_status == "success":
        assert result.feature_count == 256
        # Skip intermediate saturated queries: binary-only traversal needs 255.
        assert 100 < result.request_count <= 209
        assert result.successful_tile_count == 128
        assert any("예산" in event and "5000" in event for event in events)
    else:
        assert result.request_count == 110
        assert result.layer is None and result.feature_count == 0
        assert "110" in result.error and result.failed_tile_bbox is not None


def test_data_pagination_can_extend_initial_estimate_without_raising_server_page_size():
    definition = replace(DATA_DEFINITION, request_crs="EPSG:5186", response_crs="EPSG:5186")

    def handler(request):
        page = int(request.url.params["page"])
        assert request.url.params["size"] == "1"
        features = [_polygon_feature(f"parcel-{page}", (10, 10, 20, 20))] if page <= 101 else []
        return httpx.Response(200, json={"type": "FeatureCollection", "features": features})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = VWorldClient("secret-key", page_size=1, retries=0,
                              client=http_client).fetch_layer(definition, _aoi((0, 0, 100, 100)))
    assert result.status == "success", result.error
    assert result.feature_count == 101
    assert result.request_count == 102


@pytest.mark.parametrize(
    "planner_limits",
    [
        {"max_split_depth": 0, "minimum_tile_size_m": 10},
        {"max_split_depth": 32, "minimum_tile_size_m": 60},
    ],
)
def test_wfs_saturation_fails_when_depth_or_minimum_size_stops_splitting(planner_limits):
    definition = replace(DEFINITION, request_crs="EPSG:5186", response_crs="EPSG:5186")

    def handler(_: httpx.Request):
        return httpx.Response(
            200,
            json={
                "type": "FeatureCollection",
                "totalFeatures": 2,
                "features": [_polygon_feature("parent", (10, 10, 20, 20))],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = VWorldClient(
            "secret-key",
            default_tile_size_m=100,
            retries=0,
            client=http_client,
            **planner_limits,
        ).fetch_layer(definition, _aoi((0, 0, 100, 40)))

    assert result.status == "failed"
    assert result.layer is None
    assert result.feature_count == 0
    assert result.initial_tile_count == 1
    assert result.request_count == 1
    assert result.successful_tile_count == 0
    assert result.failed_tile_count == 1
    assert result.failed_tile_bbox == (0.0, 0.0, 100.0, 40.0)
    assert "나눌 수 없습니다" in (result.error or "")


def test_one_terminal_wfs_tile_atomically_discards_prior_tiles_and_reports_output_bbox():
    requests: list[tuple[float, float, float, float]] = []

    def handler(request: httpx.Request):
        requests.append(tuple(float(value) for value in request.url.params["bbox"].split(",")))
        if len(requests) == 1:
            return httpx.Response(
                200,
                json={
                    "type": "FeatureCollection",
                    "features": [_polygon_feature("partial", (127.01, 37.0, 127.02, 37.01))],
                },
            )
        return httpx.Response(400, text="terminal")

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = VWorldClient("secret-key", retries=0, client=http_client).fetch_layer(
            DEFINITION,
            _aoi((200_000, 489_000, 215_000, 490_000)),
        )

    assert result.status == "failed"
    assert result.layer is None
    assert result.feature_count == 0
    assert result.initial_tile_count == 2
    assert result.request_count == 2
    assert result.successful_tile_count == 1
    assert result.failed_tile_count == 1
    assert result.failed_tile_bbox == (210_000.0, 489_000.0, 215_000.0, 490_000.0)
    assert requests[1] != result.failed_tile_bbox


def test_wfs_same_id_and_content_deduplicates_with_source_derived_feature_id():
    definition = replace(DEFINITION, request_crs="EPSG:5186", response_crs="EPSG:5186")
    feature = _polygon_feature("stable-id", (10, 10, 20, 20), version=1)

    def handler(_: httpx.Request):
        return httpx.Response(
            200,
            json={"type": "FeatureCollection", "features": [feature]},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = VWorldClient(
            "secret-key",
            default_tile_size_m=100,
            retries=0,
            client=http_client,
        ).fetch_layer(definition, _aoi((0, 0, 200, 40)))

    assert result.status == "success"
    assert result.feature_count == 1
    assert result.layer is not None
    assert [item.feature_id for item in result.layer.features] == ["id:stable-id"]
    assert result.request_count == 2
    assert result.successful_tile_count == 2


def test_wfs_different_ids_with_identical_content_preserve_both_source_derived_ids():
    definition = replace(DEFINITION, request_crs="EPSG:5186", response_crs="EPSG:5186")
    attempts = 0

    def handler(_: httpx.Request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            200,
            json={
                "type": "FeatureCollection",
                "features": [
                    _polygon_feature(
                        "source-a" if attempts == 1 else "source-b",
                        (10, 10, 20, 20),
                        version=1,
                    )
                ],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = VWorldClient(
            "secret-key",
            default_tile_size_m=100,
            retries=0,
            client=http_client,
        ).fetch_layer(definition, _aoi((0, 0, 200, 40)))

    assert result.status == "success"
    assert result.feature_count == 2
    assert result.layer is not None
    assert {item.feature_id for item in result.layer.features} == {"id:source-a", "id:source-b"}
    assert result.request_count == 2
    assert result.successful_tile_count == 2


def test_wfs_same_id_with_different_content_fails_the_layer_atomically():
    definition = replace(DEFINITION, request_crs="EPSG:5186", response_crs="EPSG:5186")
    attempts = 0

    def handler(_: httpx.Request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            200,
            json={
                "type": "FeatureCollection",
                "features": [
                    _polygon_feature(
                        "stable-id",
                        (10, 10, 20, 20) if attempts == 1 else (110, 10, 120, 20),
                        version=attempts,
                    )
                ],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = VWorldClient(
            "secret-key",
            default_tile_size_m=100,
            retries=0,
            client=http_client,
        ).fetch_layer(definition, _aoi((0, 0, 200, 40)))

    assert result.status == "failed"
    assert result.layer is None
    assert result.feature_count == 0
    assert result.request_count == 2
    assert result.successful_tile_count == 1
    assert result.failed_tile_count == 1
    assert result.failed_tile_bbox == (100.0, 0.0, 200.0, 40.0)
    assert "stable-id" in (result.error or "")


def test_valid_point_for_multipolygon_definition_fails_atomically():
    definition = replace(DEFINITION, request_crs="EPSG:5186", response_crs="EPSG:5186")

    def handler(_: httpx.Request):
        return httpx.Response(
            200,
            json={
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "id": "wrong-family",
                        "properties": {},
                        "geometry": {"type": "Point", "coordinates": [10, 10]},
                    }
                ],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = VWorldClient(
            "secret-key",
            default_tile_size_m=100,
            retries=0,
            client=http_client,
        ).fetch_layer(definition, _aoi((0, 0, 100, 40)))

    assert result.status == "failed"
    assert result.layer is None
    assert result.output is None
    assert result.feature_count == 0
    assert result.request_count == 1
    assert result.successful_tile_count == 0
    assert result.failed_tile_count == 1
    assert result.failed_tile_bbox == (0.0, 0.0, 100.0, 40.0)
    assert "Point" in (result.error or "")
    assert "MultiPolygon" in (result.error or "")


def test_valid_polygon_is_normalized_for_multipolygon_output_semantics():
    definition = replace(DEFINITION, request_crs="EPSG:5186", response_crs="EPSG:5186")

    def handler(_: httpx.Request):
        return httpx.Response(
            200,
            json={
                "type": "FeatureCollection",
                "features": [_polygon_feature("polygon", (10, 10, 20, 20))],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = VWorldClient(
            "secret-key",
            default_tile_size_m=100,
            retries=0,
            client=http_client,
        ).fetch_layer(definition, _aoi((0, 0, 100, 40)))

    assert result.status == "success"
    assert result.feature_count == 1
    assert result.layer is not None
    assert result.layer.geometry_type == "MultiPolygon"
    assert result.layer.features[0].geometry.geom_type == "MultiPolygon"
    assert result.request_count == 1
    assert result.successful_tile_count == 1


def test_invalid_wfs_polygon_is_repaired_before_validation():
    def handler(_: httpx.Request):
        return httpx.Response(
            200,
            json={
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "id": "invalid-polygon",
                        "properties": {"pnu": "4691034023200440002"},
                        "geometry": {
                            "type": "MultiPolygon",
                            "coordinates": [
                                [
                                    [
                                        [127.0, 37.0],
                                        [127.02, 37.02],
                                        [127.02, 37.0],
                                        [127.0, 37.02],
                                        [127.0, 37.0],
                                    ]
                                ]
                            ],
                        },
                    }
                ],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = VWorldClient("secret-key", retries=0, client=http_client).fetch_layer(DEFINITION, _aoi())

    assert result.status == "success"
    assert result.layer is not None
    assert result.feature_count == 1
    assert result.layer.features[0].geometry.is_valid
    assert "Polygon" in result.layer.features[0].geometry.geom_type
    assert result.layer.features[0].properties["pnu"] == "4691034023200440002"


def test_invalid_source_polygon_with_collapsed_spike_keeps_all_polygon_area_and_logs_repair():
    definition = replace(DEFINITION, request_crs="EPSG:5186", response_crs="EPSG:5186")
    feature = _polygon_feature("spiked-parcel", (10, 10, 20, 20), name="original")
    # The spike travels out and back on the same edge, enclosing zero area.
    feature["geometry"]["coordinates"] = [
        [[10, 10], [20, 10], [20, 20], [15, 20], [15, 25], [15, 20], [10, 20], [10, 10]],
    ]
    events = []
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(
        200, json={"type": "FeatureCollection", "features": [feature]},
    ))) as http_client:
        result = VWorldClient("secret-key", retries=0, client=http_client,
                              on_progress=events.append).fetch_layer(definition, _aoi((0, 0, 100, 100)))
    assert result.status == "success", result.error
    assert result.feature_count == 1
    repaired = result.layer.features[0]
    assert repaired.geometry.is_valid and repaired.geometry.equals(box(10, 10, 20, 20))
    assert repaired.geometry.area == 100
    assert repaired.properties == {"name": "original"}
    assert any("spiked-parcel" in event and "복구" in event for event in events)


def test_completely_collapsed_source_polygon_still_fails_atomically():
    definition = replace(DEFINITION, request_crs="EPSG:5186", response_crs="EPSG:5186")
    feature = _polygon_feature("no-area", (10, 10, 20, 20))
    feature["geometry"]["coordinates"] = [[[10, 10], [20, 10], [30, 10], [10, 10]]]
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(
        200, json={"type": "FeatureCollection", "features": [feature]},
    ))) as http_client:
        result = VWorldClient("secret-key", retries=0, client=http_client).fetch_layer(
            definition, _aoi((0, 0, 100, 100)),
        )
    assert result.status == "failed" and result.layer is None
    assert result.feature_count == 0


def test_wfs_keeps_complete_source_geometry_and_only_filters_non_intersections():
    definition = replace(DEFINITION, request_crs="EPSG:5186", response_crs="EPSG:5186")

    def handler(request: httpx.Request):
        assert request.url.params["maxFeatures"] == "1000"
        return httpx.Response(
            200,
            json={
                "type": "FeatureCollection",
                "features": [
                    _polygon_feature("crossing", (-10, 10, 110, 20), location="crossing"),
                    _polygon_feature("outside", (120, 10, 130, 20), location="outside"),
                ],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = VWorldClient(
            "secret-key",
            page_size=5000,
            default_tile_size_m=100,
            retries=0,
            client=http_client,
        ).fetch_layer(definition, _aoi((0, 0, 100, 40)))

    assert result.status == "success"
    assert result.feature_count == 1
    assert result.layer is not None
    assert result.layer.crs == "EPSG:5186"
    [feature] = result.layer.features
    assert feature.properties["location"] == "crossing"
    assert feature.geometry.bounds == (-10.0, 10.0, 110.0, 20.0)


def test_mismatched_response_crs_fails_the_active_output_tile():
    definition = replace(DEFINITION, request_crs="EPSG:5186", response_crs="EPSG:5186")

    def handler(_: httpx.Request):
        return httpx.Response(
            200,
            json={
                "type": "FeatureCollection",
                "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
                "features": [],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = VWorldClient(
            "secret-key",
            default_tile_size_m=100,
            retries=0,
            client=http_client,
        ).fetch_layer(definition, _aoi((0, 0, 100, 40)))

    assert result.status == "failed"
    assert result.layer is None
    assert result.request_count == 1
    assert result.failed_tile_count == 1
    assert result.failed_tile_bbox == (0.0, 0.0, 100.0, 40.0)
    assert "좌표계" in (result.error or "")


def test_unparseable_response_crs_is_an_atomic_failed_result_for_the_active_output_tile():
    definition = replace(DEFINITION, request_crs="EPSG:5186", response_crs="EPSG:5186")

    def handler(_: httpx.Request):
        return httpx.Response(
            200,
            json={
                "type": "FeatureCollection",
                "crs": {"type": "name", "properties": {"name": "not-a-crs"}},
                "features": [],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = VWorldClient(
            "secret-key",
            default_tile_size_m=100,
            retries=0,
            client=http_client,
        ).fetch_layer(definition, _aoi((0, 0, 100, 40)))

    assert result.status == "failed"
    assert result.layer is None
    assert result.output is None
    assert result.feature_count == 0
    assert result.request_count == 1
    assert result.successful_tile_count == 0
    assert result.failed_tile_count == 1
    assert result.failed_tile_bbox == (0.0, 0.0, 100.0, 40.0)
    assert "VWorld 응답 좌표계" in (result.error or "")
    assert "not-a-crs" in (result.error or "")


@pytest.mark.parametrize(
    "feature",
    [
        {"type": "Feature", "id": "missing", "properties": {}},
        {
            "type": "Feature",
            "id": "malformed",
            "properties": {},
            "geometry": {"type": "DefinitelyNotGeometry", "coordinates": []},
        },
    ],
)
def test_malformed_geometry_atomically_fails_its_output_tile(feature):
    definition = replace(DEFINITION, request_crs="EPSG:5186", response_crs="EPSG:5186")

    def handler(_: httpx.Request):
        return httpx.Response(200, json={"type": "FeatureCollection", "features": [feature]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = VWorldClient(
            "secret-key",
            default_tile_size_m=100,
            retries=0,
            client=http_client,
        ).fetch_layer(definition, _aoi((0, 0, 100, 40)))

    assert result.status == "failed"
    assert result.layer is None
    assert result.feature_count == 0
    assert result.request_count == 1
    assert result.successful_tile_count == 0
    assert result.failed_tile_count == 1
    assert result.failed_tile_bbox == (0.0, 0.0, 100.0, 40.0)
    assert "Geometry" in (result.error or "")


def test_nonfinite_geometry_atomically_fails_its_output_tile():
    definition = replace(DEFINITION, request_crs="EPSG:5186", response_crs="EPSG:5186")
    feature = _polygon_feature("nonfinite", (0, 0, 10, 10))
    feature["geometry"]["coordinates"][0][0][0] = float("inf")

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"type": "FeatureCollection", "features": [feature]},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = VWorldClient(
            "secret-key",
            default_tile_size_m=100,
            retries=0,
            client=http_client,
        ).fetch_layer(definition, _aoi((0, 0, 100, 40)))

    assert result.status == "failed"
    assert result.layer is None
    assert result.request_count == 1
    assert result.failed_tile_bbox == (0.0, 0.0, 100.0, 40.0)
    assert "Geometry" in (result.error or "")


def test_http_200_xml_service_exception_is_reported_clearly():
    response = b"""<?xml version="1.0" encoding="UTF-8"?>
    <ServiceExceptionReport>
      <ServiceException code="INVALID_RANGE">STARTINDEX must be at most 1000</ServiceException>
    </ServiceExceptionReport>
    """

    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=response))) as http_client:
        result = VWorldClient("secret-key", retries=0, client=http_client).fetch_layer(DEFINITION, _aoi())

    assert result.status == "failed"
    assert "INVALID_RANGE" in (result.error or "")
    assert "STARTINDEX must be at most 1000" in (result.error or "")
    assert "JSON" not in (result.error or "")


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_xml_document_type_declaration_is_rejected(encoding: str):
    response = '''<?xml version="1.0"?>
    <!DOCTYPE root [<!ENTITY repeated "expanded">]>
    <root>&repeated;</root>
    '''.encode(encoding)

    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=response))) as http_client:
        client = VWorldClient("secret-key", retries=0, client=http_client)
        with pytest.raises(ProviderError, match="DTD"):
            client.discover_layers()


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_xml_bare_document_type_declaration_is_rejected(encoding: str):
    response = '<!DOCTYPE root><root/>'.encode(encoding)

    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=response))) as http_client:
        client = VWorldClient("secret-key", retries=0, client=http_client)
        with pytest.raises(ProviderError, match="DTD"):
            client.discover_layers()


def test_oversized_response_is_rejected_before_json_parsing():
    response = {
        "type": "FeatureCollection",
        "features": [],
        "padding": "x" * 128,
    }

    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response))) as http_client:
        result = VWorldClient(
            "secret-key",
            retries=0,
            max_response_bytes=64,
            client=http_client,
        ).fetch_layer(DEFINITION, _aoi())

    assert result.status == "failed"
    assert "응답 크기 제한" in (result.error or "")


def test_default_64_mib_content_length_guard_rejects_before_reading_body():
    def handler(_: httpx.Request):
        return httpx.Response(
            200,
            headers={"Content-Length": str(64 * 1024 * 1024 + 1)},
            content=b"{}",
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = VWorldClient("secret-key", retries=0, client=http_client).fetch_layer(DEFINITION, _aoi())

    assert result.status == "failed"
    assert result.request_count == 1
    assert "응답 크기 제한" in (result.error or "")


def test_stream_size_guard_rejects_when_content_length_is_absent():
    def handler(_: httpx.Request):
        return httpx.Response(200, stream=httpx.ByteStream(b"123456789"))

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = VWorldClient(
            "secret-key",
            retries=0,
            max_response_bytes=8,
            client=http_client,
        ).fetch_layer(DEFINITION, _aoi())

    assert result.status == "failed"
    assert result.request_count == 1
    assert "응답 크기 제한" in (result.error or "")


def test_redirect_response_is_not_followed():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request):
        requests.append(request)
        if request.url.host == "api.vworld.kr":
            return httpx.Response(302, headers={"Location": "https://redirected.example/features"})
        return httpx.Response(200, json={"type": "FeatureCollection", "features": []})

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as http_client:
        result = VWorldClient("secret-key", retries=0, client=http_client).fetch_layer(DEFINITION, _aoi())

    assert result.status == "failed"
    assert result.request_count == 1
    assert len(requests) == 1


def test_invalid_content_encoding_is_isolated_as_failed_download():
    def handler(_: httpx.Request):
        return httpx.Response(
            200,
            headers={"Content-Encoding": "gzip"},
            stream=httpx.ByteStream(b"not-a-gzip-stream"),
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = VWorldClient("secret-key", retries=0, client=http_client).fetch_layer(DEFINITION, _aoi())

    assert result.status == "failed"
    assert "VWorld 요청에 실패했습니다" in (result.error or "")


def test_api_key_is_redacted_from_http_error():
    attempts = 0

    def handler(request: httpx.Request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(401, request=request, text="invalid key")

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = VWorldClient("top-secret", retries=0, client=http_client)
        result = client.fetch_layer(DEFINITION, _aoi())

    assert result.status == "failed"
    assert result.request_count == 1
    assert attempts == 1
    assert "top-secret" not in (result.error or "")
    assert "***" in (result.error or "")


def test_scalar_vworld_error_envelope_is_an_atomic_failed_result():
    definition = replace(DATA_DEFINITION, request_crs="EPSG:5186", response_crs="EPSG:5186")

    def handler(_: httpx.Request):
        return httpx.Response(
            200,
            json={"response": {"status": "ERROR", "error": "scalar service failure"}},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = VWorldClient(
            "secret-key",
            default_tile_size_m=100,
            retries=0,
            client=http_client,
        ).fetch_layer(definition, _aoi((0, 0, 100, 40)))

    assert result.status == "failed"
    assert result.layer is None
    assert result.output is None
    assert result.feature_count == 0
    assert result.request_count == 1
    assert result.successful_tile_count == 0
    assert result.failed_tile_count == 1
    assert result.failed_tile_bbox == (0.0, 0.0, 100.0, 40.0)
    assert "scalar service failure" in (result.error or "")


def test_data_api_parameters_and_nested_feature_collection():
    def handler(request: httpx.Request):
        assert request.url.params["service"] == "data"
        assert request.url.params["data"] == "LT_C_ADSIDO_INFO"
        assert request.url.params["page"] == "1"
        assert request.url.params["geomFilter"].startswith("BOX(")
        return httpx.Response(
            200,
            json={
                "response": {
                    "status": "OK",
                    "result": {
                        "featureCollection": {
                            "type": "FeatureCollection",
                            "features": [
                                {
                                    "type": "Feature",
                                    "id": "sido-1",
                                    "properties": {"name": "서울특별시"},
                                    "geometry": {
                                        "type": "Polygon",
                                        "coordinates": [
                                            [[127.0, 37.0], [127.01, 37.0], [127.01, 37.01], [127.0, 37.0]]
                                        ],
                                    },
                                }
                            ],
                        }
                    },
                }
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = VWorldClient("secret-key", retries=0, client=http_client).fetch_layer(DATA_DEFINITION, _aoi())

    assert result.status == "success"
    assert result.feature_count == 1
    assert result.layer is not None
    assert result.layer.crs == "EPSG:5186"
    assert result.layer.features[0].properties["name"] == "서울특별시"
    assert result.initial_tile_count == 1
    assert result.request_count == 1
    assert result.successful_tile_count == 1
    assert result.failed_tile_count == 0
    assert result.failed_tile_bbox is None


def test_data_api_paginates_independently_inside_each_initial_tile():
    definition = replace(DATA_DEFINITION, request_crs="EPSG:5186", response_crs="EPSG:5186")
    requests: list[tuple[str, int]] = []

    def handler(request: httpx.Request):
        geom_filter = request.url.params["geomFilter"]
        page = int(request.url.params["page"])
        requests.append((geom_filter, page))
        min_x = float(geom_filter.removeprefix("BOX(").removesuffix(")").split(",")[0])
        tile_name = "left" if min_x == 0 else "right"
        offset = 10 if tile_name == "left" else 110
        features = (
            [
                _polygon_feature(f"{tile_name}-a", (offset, 5, offset + 2, 7), tile=tile_name),
                _polygon_feature(f"{tile_name}-b", (offset + 3, 5, offset + 5, 7), tile=tile_name),
            ]
            if page == 1
            else [_polygon_feature(f"{tile_name}-c", (offset + 6, 5, offset + 8, 7), tile=tile_name)]
        )
        return httpx.Response(200, json={"type": "FeatureCollection", "features": features})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = VWorldClient(
            "secret-key",
            page_size=2,
            default_tile_size_m=100,
            retries=0,
            client=http_client,
        ).fetch_layer(definition, _aoi((0, 0, 200, 40)))

    assert result.status == "success"
    assert result.feature_count == 6
    assert result.initial_tile_count == 2
    assert result.request_count == 4
    assert result.successful_tile_count == 2
    assert [page for _, page in requests] == [1, 2, 1, 2]
    assert requests[0][0] == requests[1][0]
    assert requests[2][0] == requests[3][0]
    assert requests[0][0] != requests[2][0]


def test_data_api_repeated_page_fingerprint_resets_for_each_tile():
    definition = replace(DATA_DEFINITION, request_crs="EPSG:5186", response_crs="EPSG:5186")
    shared = _polygon_feature("shared", (10, 5, 12, 7), value="same")

    def handler(request: httpx.Request):
        features = [shared] if request.url.params["page"] == "1" else []
        return httpx.Response(200, json={"type": "FeatureCollection", "features": features})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = VWorldClient(
            "secret-key",
            page_size=1,
            default_tile_size_m=100,
            retries=0,
            client=http_client,
        ).fetch_layer(definition, _aoi((0, 0, 200, 40)))

    assert result.status == "success"
    assert result.feature_count == 1
    assert result.request_count == 4
    assert result.successful_tile_count == 2


def test_data_api_repeated_page_inside_one_tile_fails_atomically():
    definition = replace(DATA_DEFINITION, request_crs="EPSG:5186", response_crs="EPSG:5186")
    repeated = _polygon_feature("repeated", (10, 5, 12, 7))

    def handler(_: httpx.Request):
        return httpx.Response(200, json={"type": "FeatureCollection", "features": [repeated]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = VWorldClient(
            "secret-key",
            page_size=1,
            default_tile_size_m=100,
            retries=0,
            client=http_client,
        ).fetch_layer(definition, _aoi((0, 0, 100, 40)))

    assert result.status == "failed"
    assert result.layer is None
    assert result.request_count == 2
    assert result.successful_tile_count == 0
    assert result.failed_tile_count == 1
    assert result.failed_tile_bbox == (0.0, 0.0, 100.0, 40.0)
    assert "같은 페이지" in (result.error or "")


def test_data_api_pages_past_legacy_limit_when_the_layer_budget_allows_it():
    definition = replace(DATA_DEFINITION, request_crs="EPSG:5186", response_crs="EPSG:5186")

    def handler(request: httpx.Request):
        page = int(request.url.params["page"])
        features = (
            [_polygon_feature(f"feature-{page}", (page % 90, 5, page % 90 + 0.5, 5.5), page=page)]
            if page <= 101
            else []
        )
        return httpx.Response(200, json={"type": "FeatureCollection", "features": features})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = VWorldClient(
            "secret-key",
            page_size=1,
            default_tile_size_m=100,
            request_budget_minimum=102,
            request_budget_per_tile=1,
            request_budget_maximum=102,
            retries=0,
            client=http_client,
        ).fetch_layer(definition, _aoi((0, 0, 100, 40)))

    assert result.status == "success"
    assert result.feature_count == 101
    assert result.request_count == 102
    assert result.successful_tile_count == 1


def test_data_api_same_id_with_different_content_fails_the_layer_atomically():
    definition = replace(DATA_DEFINITION, request_crs="EPSG:5186", response_crs="EPSG:5186")
    attempts = 0

    def handler(_: httpx.Request):
        nonlocal attempts
        attempts += 1
        bounds = (10, 5, 12, 7) if attempts == 1 else (110, 5, 112, 7)
        return httpx.Response(
            200,
            json={
                "type": "FeatureCollection",
                "features": [_polygon_feature("stable-id", bounds, version=attempts)],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = VWorldClient(
            "secret-key",
            page_size=2,
            default_tile_size_m=100,
            retries=0,
            client=http_client,
        ).fetch_layer(definition, _aoi((0, 0, 200, 40)))

    assert result.status == "failed"
    assert result.layer is None
    assert result.request_count == 2
    assert result.successful_tile_count == 1
    assert result.failed_tile_bbox == (100.0, 0.0, 200.0, 40.0)
    assert "stable-id" in (result.error or "")


@pytest.mark.parametrize(
    (
        "aoi_bounds",
        "minimum",
        "per_tile",
        "maximum",
        "expected_initial_tiles",
        "expected_attempts",
    ),
    [
        ((0, 0, 5, 5), 3, 1, 10, 1, 3),
        ((0, 0, 15, 5), 1, 2, 10, 2, 4),
        ((0, 0, 25, 5), 1, 4, 5, 3, 5),
    ],
)
def test_request_budget_minimum_product_and_cap_include_every_retry_attempt(
    aoi_bounds,
    minimum,
    per_tile,
    maximum,
    expected_initial_tiles,
    expected_attempts,
):
    definition = replace(DEFINITION, request_crs="EPSG:5186", response_crs="EPSG:5186")
    attempts = 0

    def handler(request: httpx.Request):
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("retry me", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = VWorldClient(
            "secret-key",
            retries=10,
            backoff=0,
            default_tile_size_m=10,
            request_budget_minimum=minimum,
            request_budget_per_tile=per_tile,
            request_budget_maximum=maximum,
            client=http_client,
        ).fetch_layer(definition, _aoi(aoi_bounds))

    assert result.status == "failed"
    assert result.initial_tile_count == expected_initial_tiles
    assert result.request_count == expected_attempts
    assert attempts == expected_attempts
    assert result.successful_tile_count == 0
    assert result.failed_tile_count == 1
    assert result.failed_tile_bbox == (
        float(aoi_bounds[0]),
        float(aoi_bounds[1]),
        min(float(aoi_bounds[2]), 10.0),
        float(aoi_bounds[3]),
    )


def test_empty_feature_collection_is_a_successful_empty_layer():
    def handler(_: httpx.Request):
        return httpx.Response(200, json={"type": "FeatureCollection", "features": []})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = VWorldClient("secret-key", retries=0, client=http_client).fetch_layer(DEFINITION, _aoi())

    assert result.status == "success"
    assert result.feature_count == 0
    assert result.layer is not None
    assert result.layer.features == []
    assert result.initial_tile_count == 1
    assert result.request_count == 1
    assert result.successful_tile_count == 1
    assert result.failed_tile_count == 0
    assert result.failed_tile_bbox is None


def test_invalid_aoi_crs_is_returned_as_atomic_failure_without_request():
    requests: list[httpx.Request] = []
    aoi = _aoi()
    aoi.crs = "not-a-crs"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _empty_collection(request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = VWorldClient(
            "secret-key",
            retries=0,
            client=http_client,
        ).fetch_layer(DEFINITION, aoi)

    assert result.status == "failed"
    assert result.layer is None
    assert result.request_count == 0
    assert requests == []
    assert "좌표계" in (result.error or "")


def test_timeout_is_retried_with_backoff():
    attempts = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise httpx.ReadTimeout("temporary timeout", request=request)
        return httpx.Response(200, json={"type": "FeatureCollection", "features": []})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = VWorldClient(
            "secret-key",
            retries=2,
            backoff=0.25,
            client=http_client,
            sleep=sleeps.append,
        ).fetch_layer(DEFINITION, _aoi())

    assert result.status == "success"
    assert attempts == 3
    assert sleeps == [0.25, 0.5]
    assert result.request_count == 3
    assert result.successful_tile_count == 1


@pytest.mark.parametrize("status_code", [429, 500, 502, 503, 504])
def test_vworld_transient_statuses_are_retried_and_counted(status_code):
    attempts = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(status_code, request=request)
        return _empty_collection(request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = VWorldClient(
            "secret-key",
            retries=1,
            backoff=0.25,
            client=http_client,
            sleep=sleeps.append,
        ).fetch_layer(DEFINITION, _aoi())

    assert result.status == "success"
    assert attempts == 2
    assert sleeps == [0.25]
    assert result.request_count == 2
    assert result.successful_tile_count == 1


def test_transient_response_is_closed_before_backoff_sleep():
    attempts = 0
    responses: list[httpx.Response] = []
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            response = httpx.Response(503, stream=httpx.ByteStream(b""))
            responses.append(response)
            return response
        return _empty_collection(request)

    def sleep(delay: float) -> None:
        assert responses[-1].is_closed
        sleeps.append(delay)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = VWorldClient(
            "secret-key",
            retries=1,
            backoff=0.25,
            client=http_client,
            sleep=sleep,
        ).fetch_layer(DEFINITION, _aoi())

    assert result.status == "success"
    assert attempts == 2
    assert responses[0].is_closed
    assert sleeps == [0.25]


def test_fetch_context_counters_do_not_leak_between_layers():
    attempts = 0

    def handler(request: httpx.Request):
        nonlocal attempts
        attempts += 1
        return _empty_collection(request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = VWorldClient("secret-key", retries=0, client=http_client)
        first = client.fetch_layer(DEFINITION, _aoi())
        second = client.fetch_layer(DEFINITION, _aoi())

    assert attempts == 2
    assert first.request_count == 1
    assert second.request_count == 1
    assert first.successful_tile_count == 1
    assert second.successful_tile_count == 1


def test_close_preserves_injected_client_and_closes_owned_client():
    injected = httpx.Client(transport=httpx.MockTransport(_empty_collection))
    VWorldClient("secret-key", client=injected).close()
    assert not injected.is_closed
    injected.close()

    with VWorldClient("secret-key") as owner:
        owned = owner._client
        assert not owned.is_closed
    assert owned.is_closed
