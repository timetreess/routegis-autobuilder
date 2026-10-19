from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from shapely.geometry import MultiPolygon, Polygon, box

from routegis.errors import ProviderError
from routegis.gis import transform_bounds, transform_geometry
from routegis.models import DownloadResult, LayerDefinition, SpatialFeature, SpatialLayer
from routegis.providers.ecology.client import EcologyClient


ENDPOINT = "https://apis.data.go.kr/B553084/ecoapi/EcologyzmpService/wfs/getEcologyzmpWFS"
SERVICE_KEY = "test-service-key"
WFS_NAMESPACE = "http://www.opengis.net/wfs"
GML_NAMESPACE = "http://www.opengis.net/gml"
FEATURE_NAMESPACE = "http://openapi.nie.re.kr"

SCALAR_PROPERTIES = {
    "eczm_grad": "1",
    "vtn_evl_grad": "A",
    "smld_evl_grad": "B",
    "tpgrph_evl_grad": "C",
    "amplt_evl_grad": "D",
    "cln_symbl": "N01",
    "plnt_cln_ttle": "식생군락",
    "frph_agcl_code": "AG01",
    "precise_smld_ttle": "정밀소분류",
    "census_ttle": "조사구",
    "tpgrph_ttle": "지형평가",
}

DEFINITION = LayerDefinition(
    id="ecology_nature",
    name="생태자연도",
    provider="ecology",
    service_type="wfs",
    endpoint=ENDPOINT,
    data_id="tbl_opn_eczm",
    geometry_type="MultiPolygon",
    category="ecology",
    request_crs="EPSG:5186",
    response_crs="EPSG:5186",
    publisher="국립생태원",
    dataset_id="15057288",
    license_name="이용허락범위 제한 없음",
    license_url="https://www.data.go.kr/data/15057288/openapi.do",
)

EMPTY_COLLECTION = b"""<?xml version="1.0" encoding="UTF-8"?>
<wfs:FeatureCollection
    xmlns:wfs="http://www.opengis.net/wfs"
    xmlns:gml="http://www.opengis.net/gml"
    xmlns:eco="http://apis.data.go.kr/B553084/ecoapi" />
"""


def _aoi(bounds=(200_000.0, 500_000.0, 200_100.0, 500_100.0)) -> SpatialLayer:
    return SpatialLayer(
        name="route_buffer",
        display_name="사업구간 주변 범위",
        crs="EPSG:5186",
        geometry_type="Polygon",
        category="route",
        features=[SpatialFeature(box(*bounds))],
    )


def _empty_response(_: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=EMPTY_COLLECTION)


@pytest.mark.parametrize("supplied_key", ["fixture+/=key", "fixture%2B%2F%3Dkey", "fixture%2b%2f%3dkey"])
def test_service_key_from_portal_is_encoded_exactly_once(supplied_key):
    requests = []

    def handler(request):
        requests.append(request)
        return _empty_response(request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = EcologyClient(supplied_key, client=http_client).fetch_layer(DEFINITION, _aoi())

    assert result.status == "success"
    assert requests[0].url.params["serviceKey"] == "fixture+/=key"
    assert b"%25" not in requests[0].url.query


@pytest.mark.parametrize("status", [200, 403])
def test_portal_authentication_error_preserves_reason_code_and_message(status):
    content = b"""<OpenAPI_ServiceResponse><cmmMsgHeader>
      <errMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</errMsg>
      <returnAuthMsg>Unregistered service key</returnAuthMsg>
      <returnReasonCode>30</returnReasonCode>
    </cmmMsgHeader></OpenAPI_ServiceResponse>"""

    result = _fetch(lambda _: httpx.Response(status, content=content), retries=0)

    assert result.status == "failed"
    assert "SERVICE_KEY_IS_NOT_REGISTERED_ERROR" in (result.error or "")
    assert "30" in (result.error or "")
    if status == 403:
        assert "HTTP 403" in (result.error or "")


def test_encoded_service_key_is_redacted_in_decoded_and_encoded_errors():
    supplied_key = "fixture%2B%2F%3Dsecret"

    def handler(request):
        raise httpx.ConnectError(f"fixture+/=secret {supplied_key} {request.url}", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = EcologyClient(supplied_key, client=http_client, retries=0).fetch_layer(DEFINITION, _aoi())

    assert result.status == "failed"
    assert "fixture" not in (result.error or "")


def _fetch(
    handler: Callable[[httpx.Request], httpx.Response],
    **client_options: object,
):
    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        return EcologyClient(
            SERVICE_KEY,
            client=http_client,
            **client_options,
        ).fetch_layer(DEFINITION, _aoi())


class _ChunkedStream(httpx.SyncByteStream):
    def __init__(self, *chunks: bytes):
        self._chunks = chunks

    def __iter__(self) -> Iterator[bytes]:
        yield from self._chunks


def _coordinates(text: str, attributes: str = "") -> str:
    separator = f" {attributes.strip()}" if attributes.strip() else ""
    return f"<gml:coordinates{separator}>{text}</gml:coordinates>"


def _polygon_xml(
    exterior: str,
    *interiors: str,
    coordinate_attributes: str = "",
) -> str:
    inner_xml = "".join(
        "<gml:innerBoundaryIs><gml:LinearRing>"
        f"{_coordinates(interior, coordinate_attributes)}"
        "</gml:LinearRing></gml:innerBoundaryIs>"
        for interior in interiors
    )
    return (
        "<gml:polygonMember><gml:Polygon>"
        "<gml:outerBoundaryIs><gml:LinearRing>"
        f"{_coordinates(exterior, coordinate_attributes)}"
        "</gml:LinearRing></gml:outerBoundaryIs>"
        f"{inner_xml}"
        "</gml:Polygon></gml:polygonMember>"
    )


def _multi_polygon_xml(
    polygons: list[str],
    *,
    srs_name: str | None = "EPSG:5186",
) -> str:
    srs = f' srsName="{srs_name}"' if srs_name is not None else ""
    return f"<gml:MultiPolygon{srs}>{''.join(polygons)}</gml:MultiPolygon>"


def _feature_xml(
    *,
    fid: str = "tbl_opn_eczm.1",
    geometry_xml: str | None = None,
    properties: dict[str, str] | None = None,
    feature_name: str = "tbl_opn_eczm",
    feature_namespace: str = FEATURE_NAMESPACE,
) -> str:
    if geometry_xml is None:
        geometry_xml = _multi_polygon_xml(
            [_polygon_xml("200010,500010 200090,500010 200090,500090 200010,500090 200010,500010")]
        )
    property_xml = "".join(
        f"<open:{name}>{value}</open:{name}>"
        for name, value in (properties if properties is not None else SCALAR_PROPERTIES).items()
    )
    return (
        f'<open:{feature_name} xmlns:open="{feature_namespace}" fid="{fid}">'
        f"<open:geom>{geometry_xml}</open:geom>"
        f"{property_xml}"
        f"</open:{feature_name}>"
    )


def _collection_xml(*member_contents: str, wfs_namespace: str = WFS_NAMESPACE) -> bytes:
    members = "".join(
        f"<gml:featureMember>{member_content}</gml:featureMember>"
        for member_content in member_contents
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<wfs:FeatureCollection xmlns:wfs="{wfs_namespace}" '
        f'xmlns:gml="{GML_NAMESPACE}">{members}</wfs:FeatureCollection>'
    ).encode("utf-8")


def test_ecobank_namespace_from_live_service_keeps_geometry_and_properties():
    result = _fetch(lambda _: httpx.Response(200, content=_collection_xml(
        _feature_xml(feature_namespace="kr.nie-ecobank"),
    )))
    assert result.status == "success", result.error
    assert result.feature_count == 1
    assert result.layer.features[0].properties == SCALAR_PROPERTIES


@pytest.mark.parametrize("namespace,expected_status", [
    (GML_NAMESPACE, "success"), ("https://foreign.example/gml", "failed"),
])
def test_live_gml_feature_bounds_are_metadata_not_scalar_attributes(namespace, expected_status):
    bounds = (f'<metadata:boundedBy xmlns:metadata="{namespace}">'
              '<gml:Box srsName="http://www.opengis.net/gml/srs/epsg.xml#5186">'
              '<gml:coordinates>200010,500010 200090,500090</gml:coordinates>'
              '</gml:Box></metadata:boundedBy>')
    feature = _feature_xml(feature_namespace="kr.nie-ecobank").replace(
        "<open:geom>", f"{bounds}<open:geom>", 1,
    ).replace('srsName="EPSG:5186"', 'srsName="http://www.opengis.net/gml/srs/epsg.xml#5186"')
    result = _fetch_xml(_collection_xml(feature))
    assert result.status == expected_status, result.error
    if expected_status == "success":
        assert result.feature_count == 1
        assert result.layer.features[0].properties == SCALAR_PROPERTIES
    else:
        assert result.layer is None


def _fetch_xml(
    content: bytes,
    *,
    definition: LayerDefinition = DEFINITION,
    aoi: SpatialLayer | None = None,
    service_key: str = SERVICE_KEY,
    **client_options: object,
):
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=content)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        return EcologyClient(
            service_key,
            retries=0,
            client=http_client,
            **client_options,
        ).fetch_layer(definition, aoi or _aoi())


def _assert_atomic_failure(
    result: DownloadResult,
    expected_bbox=(200_000.0, 500_000.0, 200_100.0, 500_100.0),
) -> None:
    assert result.status == "failed"
    assert result.layer is None
    assert result.feature_count == 0
    assert result.request_count == 1
    assert result.successful_tile_count == 0
    assert result.failed_tile_count == 1
    assert result.failed_tile_bbox == expected_bbox


@pytest.mark.parametrize("service_key", ["", " ", "\t\r\n"])
def test_constructor_requires_nonblank_service_key(service_key: str):
    with pytest.raises(ProviderError, match="인증키"):
        EcologyClient(service_key)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://apis.data.go.kr/B553084/ecoapi/EcologyzmpService/wfs/getEcologyzmpWFS",
        "https://alternate.example/wfs/getEcologyzmpWFS",
        "https://apis.data.go.kr/B553084/ecoapi/EcologyzmpService/wfs/alternatePath",
    ],
    ids=["http", "alternate-host", "alternate-path"],
)
def test_constructor_has_no_public_endpoint_override(endpoint: str):
    options: dict[str, object] = {"endpoint": endpoint}
    with httpx.Client(transport=httpx.MockTransport(_empty_response)) as http_client:
        with pytest.raises(TypeError, match="endpoint"):
            EcologyClient(SERVICE_KEY, client=http_client, **options)


def test_constructor_rejects_response_limit_above_absolute_64_mib_cap():
    with httpx.Client(transport=httpx.MockTransport(_empty_response)) as http_client:
        with pytest.raises(ValueError, match="max_response_bytes"):
            EcologyClient(
                SERVICE_KEY,
                max_response_bytes=67_108_865,
                client=http_client,
            )


def test_default_endpoint_and_exact_get_params_are_httpx_encoded():
    requests: list[httpx.Request] = []
    encoded_key = "fixture key+/="

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=EMPTY_COLLECTION)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = EcologyClient(
            encoded_key,
            page_size=999,
            client=http_client,
        ).fetch_layer(DEFINITION, _aoi())

    assert result.status == "success"
    assert result.feature_count == 0
    assert result.initial_tile_count == 1
    assert result.request_count == 1
    assert result.successful_tile_count == 1
    assert result.failed_tile_count == 0
    assert result.failed_tile_bbox is None
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET"
    assert request.url.scheme == "https"
    assert request.url.host == "apis.data.go.kr"
    assert request.url.path == "/B553084/ecoapi/EcologyzmpService/wfs/getEcologyzmpWFS"
    assert dict(request.url.params) == {
        "serviceKey": encoded_key,
        "typeName": "tbl_opn_eczm",
        "bbox": "200000.0,500000.0,200100.0,500100.0",
        "maxFeatures": "500",
    }
    assert b"serviceKey=fixture%20key%2B%2F%3D" in request.url.query
    assert encoded_key not in repr(result)


@pytest.mark.parametrize(
    "definition_endpoint",
    [
        "http://apis.data.go.kr/B553084/ecoapi/EcologyzmpService/wfs/getEcologyzmpWFS",
        "https://alternate.example/wfs/getEcologyzmpWFS",
        "https://apis.data.go.kr/B553084/ecoapi/EcologyzmpService/wfs/alternatePath",
    ],
    ids=["http", "alternate-host", "alternate-path"],
)
def test_layer_definition_endpoint_cannot_redirect_credentials(
    definition_endpoint: str,
):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=EMPTY_COLLECTION)

    definition = replace(DEFINITION, endpoint=definition_endpoint)
    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = EcologyClient(SERVICE_KEY, client=http_client).fetch_layer(
            definition,
            _aoi(),
        )

    assert result.status == "success"
    assert len(requests) == 1
    assert requests[0].url.scheme == "https"
    assert requests[0].url.host == "apis.data.go.kr"
    assert requests[0].url.path == (
        "/B553084/ecoapi/EcologyzmpService/wfs/getEcologyzmpWFS"
    )


def test_timeout_transport_failures_retry_with_backoff_and_count_every_attempt():
    attempts = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise httpx.ReadTimeout("temporary timeout", request=request)
        return httpx.Response(200, content=EMPTY_COLLECTION)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = EcologyClient(
            SERVICE_KEY,
            retries=3,
            backoff=0.5,
            client=http_client,
            sleep=sleeps.append,
        ).fetch_layer(DEFINITION, _aoi())

    assert result.status == "success"
    assert attempts == 3
    assert sleeps == [0.5, 1.0]
    assert result.request_count == 3
    assert result.successful_tile_count == 1


@pytest.mark.parametrize("status_code", [429, 500, 502, 503, 504])
def test_ecology_transient_statuses_retry(status_code: int):
    attempts = 0
    sleeps: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(status_code)
        return httpx.Response(200, content=EMPTY_COLLECTION)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = EcologyClient(
            SERVICE_KEY,
            retries=1,
            backoff=0.25,
            client=http_client,
            sleep=sleeps.append,
        ).fetch_layer(DEFINITION, _aoi())

    assert result.status == "success"
    assert attempts == 2
    assert sleeps == [0.25]
    assert result.request_count == 2


def test_exhausted_transient_status_is_terminal_after_configured_retries():
    attempts = 0
    sleeps: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = EcologyClient(
            SERVICE_KEY,
            retries=2,
            max_split_depth=0,
            backoff=0.5,
            client=http_client,
            sleep=sleeps.append,
        ).fetch_layer(DEFINITION, _aoi())

    assert result.status == "failed"
    assert result.layer is None
    assert attempts == 3
    assert sleeps == [0.5, 1.0]
    assert result.request_count == 3
    assert result.successful_tile_count == 0
    assert result.failed_tile_count == 1
    assert result.failed_tile_bbox == (200000.0, 500000.0, 200100.0, 500100.0)


def test_non_transient_http_failure_does_not_retry_or_expose_query():
    attempts = 0
    sleeps: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(400)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = EcologyClient(
            SERVICE_KEY,
            retries=3,
            client=http_client,
            sleep=sleeps.append,
        ).fetch_layer(DEFINITION, _aoi())

    assert result.status == "failed"
    assert attempts == 1
    assert sleeps == []
    assert result.request_count == 1
    assert "400" in (result.error or "")
    assert SERVICE_KEY not in (result.error or "")
    assert "serviceKey=" not in (result.error or "")
    assert ENDPOINT not in (result.error or "")


def test_transport_error_redacts_key_and_full_request_url():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(
            f"connection rejected for {SERVICE_KEY}: {request.url}",
            request=request,
        )

    result = _fetch(handler, retries=0)

    assert result.status == "failed"
    assert "ConnectError" in (result.error or "")
    assert SERVICE_KEY not in (result.error or "")
    assert "serviceKey=" not in (result.error or "")
    assert ENDPOINT not in (result.error or "")


@pytest.mark.parametrize("status_code", [301, 302, 307, 308])
def test_redirects_are_terminal_and_not_followed(status_code: int):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status_code, headers={"Location": "https://redirect.invalid/secret"})

    result = _fetch(handler, retries=3)

    assert result.status == "failed"
    assert len(requests) == 1
    assert result.request_count == 1
    assert result.failed_tile_count == 1
    assert "redirect.invalid" not in (result.error or "")


def test_declared_content_length_limit_is_enforced_before_reading_body():
    stream = _ChunkedStream(EMPTY_COLLECTION)

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Length": "9"},
            stream=stream,
        )

    result = _fetch(handler, retries=0, max_response_bytes=8)

    assert result.status == "failed"
    assert result.request_count == 1
    assert "크기" in (result.error or "")


def test_streamed_response_limit_is_enforced_cumulatively():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=_ChunkedStream(b"1234", b"5678", b"9"))

    result = _fetch(handler, retries=0, max_response_bytes=8)

    assert result.status == "failed"
    assert result.request_count == 1
    assert "크기" in (result.error or "")


def test_close_does_not_close_injected_httpx_client():
    http_client = httpx.Client(transport=httpx.MockTransport(_empty_response))
    try:
        client = EcologyClient(SERVICE_KEY, client=http_client)
        client.close()
        assert not http_client.is_closed
    finally:
        http_client.close()


def test_transient_response_is_closed_before_backoff_sleep():
    attempts = 0
    responses: list[httpx.Response] = []
    sleeps: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            response = httpx.Response(503, stream=_ChunkedStream(b""))
            responses.append(response)
            return response
        return httpx.Response(200, content=EMPTY_COLLECTION)

    def sleep(delay: float) -> None:
        assert responses[-1].is_closed
        sleeps.append(delay)

    result = _fetch(handler, retries=1, backoff=0.25, sleep=sleep)

    assert result.status == "success"
    assert attempts == 2
    assert responses[0].is_closed
    assert sleeps == [0.25]


def test_context_manager_closes_only_internally_created_httpx_client(monkeypatch):
    created: list[httpx.Client] = []
    real_client = httpx.Client

    def client_factory(**options: object) -> httpx.Client:
        assert options["follow_redirects"] is False
        timeout = options["timeout"]
        assert isinstance(timeout, httpx.Timeout)
        assert timeout.connect == 30.0
        client = real_client(transport=httpx.MockTransport(_empty_response))
        created.append(client)
        return client

    monkeypatch.setattr("routegis.providers.ecology.client.httpx.Client", client_factory)

    with EcologyClient(SERVICE_KEY) as client:
        assert client is not None
        assert not created[0].is_closed

    assert created[0].is_closed


def test_official_fixture_preserves_fid_hole_and_all_scalar_fields(
    fixture_directory: Path,
):
    content = (fixture_directory / "ecology_wfs_response.xml").read_bytes()

    result = _fetch_xml(content)

    assert result.status == "success"
    assert result.feature_count == 1
    assert result.layer is not None
    assert result.layer.source == "ecology"
    assert result.layer.crs == "EPSG:5186"
    assert result.layer.geometry_type == "MultiPolygon"
    feature = result.layer.features[0]
    assert feature.feature_id == "tbl_opn_eczm.1"
    assert feature.properties == SCALAR_PROPERTIES
    assert feature.geometry.geom_type == "MultiPolygon"
    assert len(feature.geometry.geoms) == 1
    polygon = feature.geometry.geoms[0]
    assert len(polygon.interiors) == 1
    assert list(polygon.exterior.coords)[0] == list(polygon.exterior.coords)[-1]
    assert list(polygon.interiors[0].coords)[0] == list(polygon.interiors[0].coords)[-1]


def test_official_namespace_accepts_different_prefixes(fixture_directory: Path):
    content = (fixture_directory / "ecology_wfs_response.xml").read_bytes()
    content = (
        content.replace(b"xmlns:wfs", b"xmlns:collection")
        .replace(b"wfs:", b"collection:")
        .replace(b"xmlns:gml", b"xmlns:shape")
        .replace(b"gml:", b"shape:")
        .replace(b"xmlns:open", b"xmlns:record")
        .replace(b"open:", b"record:")
    )

    result = _fetch_xml(content)

    assert result.status == "success"
    assert result.feature_count == 1
    assert result.layer is not None
    assert result.layer.features[0].feature_id == "tbl_opn_eczm.1"


def _schema_boundary_cases() -> list[object]:
    valid = _feature_xml()
    return [
        pytest.param(
            _feature_xml(feature_namespace="https://wrong.example/ecology"),
            id="wrong-feature-namespace",
        ),
        pytest.param(
            valid.replace("<open:geom>", "<open:shape>").replace(
                "</open:geom>",
                "</open:shape>",
            ),
            id="wrong-geometry-property-name",
        ),
        pytest.param(
            valid.replace(
                "<open:geom>",
                '<foreign:geom xmlns:foreign="https://wrong.example/ecology">',
            ).replace("</open:geom>", "</foreign:geom>"),
            id="wrong-geometry-property-namespace",
        ),
        pytest.param(
            valid.replace("<open:geom>", "<open:geom><open:wrapper>").replace(
                "</open:geom>",
                "</open:wrapper></open:geom>",
            ),
            id="nested-geometry-wrapper",
        ),
        pytest.param(
            valid.replace("</open:geom>", "<open:extra /></open:geom>"),
            id="extra-direct-geometry-child",
        ),
        pytest.param(
            valid.replace("<open:geom>", "").replace("</open:geom>", ""),
            id="multipolygon-without-geom-property",
        ),
    ]


@pytest.mark.parametrize("member_content", _schema_boundary_cases())
def test_feature_and_geometry_require_exact_official_direct_qnames(
    member_content: str,
):
    result = _fetch_xml(_collection_xml(member_content))

    _assert_atomic_failure(result)
    assert any(
        marker in (result.error or "")
        for marker in ("Feature", "geom", "Geometry", "MultiPolygon")
    )


def test_open_ring_is_closed_and_default_coordinate_separators_are_used():
    exterior = "200010,500010\n200090,500010 200090,500090\t200010,500090"
    content = _collection_xml(
        _feature_xml(geometry_xml=_multi_polygon_xml([_polygon_xml(exterior)]))
    )

    result = _fetch_xml(content)

    assert result.status == "success"
    assert result.layer is not None
    coordinates = list(result.layer.features[0].geometry.geoms[0].exterior.coords)
    assert len(coordinates) == 5
    assert coordinates[0] == coordinates[-1]


def test_explicit_gml2_coordinate_separators_and_decimal_marker_are_respected():
    exterior = (
        "200010,5;500010,5|200090,5;500010,5|200090,5;500090,5|"
        "200010,5;500090,5|200010,5;500010,5"
    )
    polygon = _polygon_xml(
        exterior,
        coordinate_attributes='decimal="," cs=";" ts="|"',
    )

    result = _fetch_xml(_collection_xml(_feature_xml(geometry_xml=_multi_polygon_xml([polygon]))))

    assert result.status == "success"
    assert result.layer is not None
    assert result.layer.features[0].geometry.bounds == (
        200010.5,
        500010.5,
        200090.5,
        500090.5,
    )


def test_every_polygon_member_is_preserved_and_geometry_is_not_clipped():
    first = _polygon_xml(
        "200010,500010 200090,500010 200090,500090 200010,500090 200010,500010"
    )
    second = _polygon_xml(
        "200120,500010 200140,500010 200140,500030 200120,500030 200120,500010"
    )
    content = _collection_xml(
        _feature_xml(geometry_xml=_multi_polygon_xml([first, second]))
    )

    result = _fetch_xml(
        content,
        aoi=_aoi((200000.0, 500000.0, 200050.0, 500050.0)),
    )

    assert result.status == "success"
    assert result.feature_count == 1
    assert result.layer is not None
    geometry = result.layer.features[0].geometry
    assert len(geometry.geoms) == 2
    assert geometry.bounds == (200010.0, 500010.0, 200140.0, 500090.0)


def test_polygonal_make_valid_result_is_recovered_as_multipolygon():
    bow_tie = (
        "200010,500010 200010,500090 200050,500050 200090,500090 "
        "200090,500010 200050,500050 200010,500010"
    )
    content = _collection_xml(
        _feature_xml(geometry_xml=_multi_polygon_xml([_polygon_xml(bow_tie)]))
    )

    result = _fetch_xml(content)

    assert result.status == "success"
    assert result.layer is not None
    geometry = result.layer.features[0].geometry
    assert geometry.geom_type == "MultiPolygon"
    assert len(geometry.geoms) == 2
    assert geometry.is_valid


@pytest.mark.parametrize(
    "exterior",
    [
        "200010,500010 200090,500010,7 200090,500090 200010,500010",
        "200010,500010 nope,500010 200090,500090 200010,500010",
        "200010,500010 NaN,500010 200090,500090 200010,500010",
    ],
)
def test_malformed_or_nonfinite_exterior_coordinates_fail_atomically(exterior: str):
    content = _collection_xml(
        _feature_xml(geometry_xml=_multi_polygon_xml([_polygon_xml(exterior)]))
    )

    result = _fetch_xml(content)

    _assert_atomic_failure(result)
    assert "좌표" in (result.error or "")


@pytest.mark.parametrize(
    "interior",
    [
        "200020,500020 200030,bad 200030,500030 200020,500020",
        "200020,500020 200030,500020 200020,500020",
        "200020,500020 Infinity,500020 200030,500030 200020,500020",
    ],
)
def test_malformed_or_degenerate_interior_ring_fails_atomically(interior: str):
    exterior = "200010,500010 200090,500010 200090,500090 200010,500090 200010,500010"
    polygon = _polygon_xml(exterior, interior)

    result = _fetch_xml(
        _collection_xml(_feature_xml(geometry_xml=_multi_polygon_xml([polygon])))
    )

    _assert_atomic_failure(result)
    assert "내부" in (result.error or "") or "좌표" in (result.error or "")


@pytest.mark.parametrize(
    "exterior",
    [
        "200010,500010 200020,500020 200010,500010",
        "200010,500010 200020,500020 200030,500030 200010,500010",
    ],
)
def test_degenerate_exterior_ring_fails_atomically(exterior: str):
    result = _fetch_xml(
        _collection_xml(
            _feature_xml(geometry_xml=_multi_polygon_xml([_polygon_xml(exterior)]))
        )
    )

    _assert_atomic_failure(result)
    assert "외부" in (result.error or "") or "복구" in (result.error or "")


def test_make_valid_mixed_geometry_result_is_rejected_without_dropping_parts():
    mixed_result = (
        "200000,500002 200000,500001 200002,500000 200000,500000 200000,500002"
    )
    result = _fetch_xml(
        _collection_xml(
            _feature_xml(geometry_xml=_multi_polygon_xml([_polygon_xml(mixed_result)]))
        ),
        aoi=_aoi((199999.0, 499999.0, 200010.0, 500010.0)),
    )

    _assert_atomic_failure(result, (199999.0, 499999.0, 200010.0, 500010.0))
    assert "복구" in (result.error or "")


def test_wrong_geometry_family_fails_atomically():
    line = (
        '<gml:LineString srsName="EPSG:5186">'
        '<gml:coordinates>200010,500010 200090,500090</gml:coordinates>'
        "</gml:LineString>"
    )

    result = _fetch_xml(_collection_xml(_feature_xml(geometry_xml=line)))

    _assert_atomic_failure(result)
    assert "MultiPolygon" in (result.error or "")


@pytest.mark.parametrize(
    "geometry_xml",
    [
        "",
        _multi_polygon_xml(
            [_polygon_xml("200010,500010 200090,500010 200090,500090 200010,500010")]
        )
        + _multi_polygon_xml(
            [_polygon_xml("200020,500020 200080,500020 200080,500080 200020,500020")]
        ),
    ],
    ids=["missing", "ambiguous"],
)
def test_missing_or_ambiguous_geometry_fails_atomically(geometry_xml: str):
    result = _fetch_xml(_collection_xml(_feature_xml(geometry_xml=geometry_xml)))

    _assert_atomic_failure(result)
    assert "Geometry" in (result.error or "")


@pytest.mark.parametrize(
    "member_content",
    [
        "",
        _feature_xml() + _feature_xml(fid="tbl_opn_eczm.2"),
        _feature_xml(feature_name="other_layer"),
    ],
    ids=["missing", "ambiguous", "wrong-local-name"],
)
def test_missing_ambiguous_or_wrong_feature_fails_atomically(member_content: str):
    result = _fetch_xml(_collection_xml(member_content))

    _assert_atomic_failure(result)
    assert "Feature" in (result.error or "")


@pytest.mark.parametrize("fid", ["", " ", "\t"])
def test_missing_or_blank_fid_fails_atomically(fid: str):
    result = _fetch_xml(_collection_xml(_feature_xml(fid=fid)))

    _assert_atomic_failure(result)
    assert "fid" in (result.error or "")


@pytest.mark.parametrize(
    "srs_name",
    [None, "EPSG:4326", "urn:example:unknown-crs"],
    ids=["missing", "mismatch", "unknown"],
)
def test_missing_mismatched_or_unknown_geometry_crs_fails_atomically(
    srs_name: str | None,
):
    geometry = _multi_polygon_xml(
        [_polygon_xml("200010,500010 200090,500010 200090,500090 200010,500010")],
        srs_name=srs_name,
    )

    result = _fetch_xml(_collection_xml(_feature_xml(geometry_xml=geometry)))

    _assert_atomic_failure(result)
    assert "좌표계" in (result.error or "")


@pytest.mark.parametrize("srs_name", [
    "urn:ogc:def:crs:EPSG::5186",
    "http://www.opengis.net/gml/srs/epsg.xml#5186",
    "https://www.opengis.net/gml/srs/epsg.xml#5186",
])
def test_equivalent_crs_urn_and_gml2_url_are_normalized_before_comparison(srs_name):
    geometry = _multi_polygon_xml(
        [_polygon_xml("200010,500010 200090,500010 200090,500090 200010,500010")],
        srs_name=srs_name,
    )

    result = _fetch_xml(_collection_xml(_feature_xml(geometry_xml=geometry)))

    assert result.status == "success"
    assert result.feature_count == 1


@pytest.mark.parametrize("srs_name", [
    "http://www.opengis.net/gml/srs/epsg.xml#4326",
    "http://foreign.example/gml/srs/epsg.xml#5186",
    "http://www.opengis.net/gml/srs/epsg.xml#5186?other=4326",
])
def test_gml2_crs_url_does_not_bypass_mismatch_or_unknown_crs_checks(srs_name):
    geometry = _multi_polygon_xml(
        [_polygon_xml("200010,500010 200090,500010 200090,500090 200010,500010")],
        srs_name=srs_name,
    )
    result = _fetch_xml(_collection_xml(_feature_xml(geometry_xml=geometry)))
    _assert_atomic_failure(result)
    assert "좌표계" in result.error


def test_feature_collection_requires_the_wfs_namespace_uri():
    result = _fetch_xml(
        _collection_xml(_feature_xml(), wfs_namespace="https://invalid.example/wfs")
    )

    _assert_atomic_failure(result)
    assert "FeatureCollection" in (result.error or "")


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_dtd_and_entity_declarations_are_refused_for_all_encodings(encoding: str):
    declaration = encoding.upper()
    xml = f'''<?xml version="1.0" encoding="{declaration}"?>
<!DOCTYPE wfs:FeatureCollection [<!ENTITY injected "must-not-expand">]>
<wfs:FeatureCollection xmlns:wfs="{WFS_NAMESPACE}" xmlns:gml="{GML_NAMESPACE}">
  &injected;
</wfs:FeatureCollection>'''

    result = _fetch_xml(xml.encode(encoding))

    _assert_atomic_failure(result)
    assert "DTD" in (result.error or "") or "엔터티" in (result.error or "")
    assert "must-not-expand" not in (result.error or "")


def test_non_xml_response_is_a_terminal_atomic_parse_failure():
    result = _fetch_xml(b'{"features": []}')

    _assert_atomic_failure(result)
    assert "XML" in (result.error or "")


def test_xml_service_error_is_terminal_and_redacts_key_and_url():
    content = f'''<?xml version="1.0" encoding="UTF-8"?>
<ows:ExceptionReport xmlns:ows="http://www.opengis.net/ows">
  <ows:Exception exceptionCode="InvalidParameterValue" locator="serviceKey">
    <ows:ExceptionText>{SERVICE_KEY} rejected at {ENDPOINT}?serviceKey={SERVICE_KEY}</ows:ExceptionText>
  </ows:Exception>
</ows:ExceptionReport>'''.encode("utf-8")

    result = _fetch_xml(content)

    _assert_atomic_failure(result)
    assert "InvalidParameterValue" in (result.error or "")
    assert SERVICE_KEY not in (result.error or "")
    assert ENDPOINT not in (result.error or "")
    assert "serviceKey=" not in (result.error or "")


def test_same_fid_with_canonically_equal_content_is_deduplicated():
    forward = "200010,500010 200090,500010 200090,500090 200010,500090 200010,500010"
    reverse = "200090,500090 200090,500010 200010,500010 200010,500090 200090,500090"
    reversed_properties = dict(reversed(list(SCALAR_PROPERTIES.items())))
    first = _feature_xml(
        fid="tbl_opn_eczm.same",
        geometry_xml=_multi_polygon_xml([_polygon_xml(forward)]),
    )
    second = _feature_xml(
        fid="tbl_opn_eczm.same",
        geometry_xml=_multi_polygon_xml([_polygon_xml(reverse)]),
        properties=reversed_properties,
    )

    result = _fetch_xml(_collection_xml(first, second))

    assert result.status == "success"
    assert result.feature_count == 1
    assert result.layer is not None
    assert result.layer.features[0].feature_id == "tbl_opn_eczm.same"


def test_different_fids_with_identical_content_remain_separate():
    result = _fetch_xml(
        _collection_xml(
            _feature_xml(fid="tbl_opn_eczm.1"),
            _feature_xml(fid="tbl_opn_eczm.2"),
        )
    )

    assert result.status == "success"
    assert result.feature_count == 2
    assert result.layer is not None
    assert [feature.feature_id for feature in result.layer.features] == [
        "tbl_opn_eczm.1",
        "tbl_opn_eczm.2",
    ]


def test_same_fid_with_different_content_fails_atomically():
    changed_properties = {**SCALAR_PROPERTIES, "eczm_grad": "2"}
    result = _fetch_xml(
        _collection_xml(
            _feature_xml(fid="tbl_opn_eczm.same"),
            _feature_xml(fid="tbl_opn_eczm.same", properties=changed_properties),
        )
    )

    _assert_atomic_failure(result)
    assert "fid" in (result.error or "")
    assert "tbl_opn_eczm.same" in (result.error or "")


def _feature_in_bounds(
    fid: str,
    bounds: tuple[float, float, float, float],
) -> str:
    min_x, min_y, max_x, max_y = bounds
    coordinates = (
        f"{min_x},{min_y} {max_x},{min_y} {max_x},{max_y} "
        f"{min_x},{max_y} {min_x},{min_y}"
    )
    return _feature_xml(
        fid=fid,
        geometry_xml=_multi_polygon_xml([_polygon_xml(coordinates)]),
    )


def _saturated_malformed_collection() -> bytes:
    return _collection_xml(
        *[
            f'<open:discarded xmlns:open="{FEATURE_NAMESPACE}" fid="parent.{index}" />'
            for index in range(500)
        ]
    )


def test_empty_feature_collection_is_a_successful_leaf_with_all_statistics():
    result = _fetch_xml(EMPTY_COLLECTION)

    assert result.status == "success"
    assert result.feature_count == 0
    assert result.layer is not None
    assert result.layer.source == "ecology"
    assert result.initial_tile_count == 1
    assert result.request_count == 1
    assert result.successful_tile_count == 1
    assert result.failed_tile_count == 0
    assert result.failed_tile_bbox is None


def test_default_ten_kilometre_tiles_are_planned_in_output_crs_then_transformed():
    output_bounds = (1_000_000.0, 2_000_000.0, 1_015_000.0, 2_000_100.0)
    aoi = SpatialLayer(
        name="aoi",
        display_name="다른 출력 좌표계",
        crs="EPSG:5179",
        geometry_type="Polygon",
        category="route",
        features=[SpatialFeature(box(*output_bounds))],
    )
    requested_bboxes: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_bboxes.append(request.url.params["bbox"])
        return httpx.Response(200, content=EMPTY_COLLECTION)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = EcologyClient(SERVICE_KEY, client=http_client).fetch_layer(DEFINITION, aoi)

    output_tiles = [
        (1_000_000.0, 2_000_000.0, 1_010_000.0, 2_000_100.0),
        (1_010_000.0, 2_000_000.0, 1_015_000.0, 2_000_100.0),
    ]
    expected_bboxes = [
        ",".join(str(value) for value in transform_bounds(tile, "EPSG:5179", "EPSG:5186"))
        for tile in output_tiles
    ]
    assert result.status == "success"
    assert result.initial_tile_count == 2
    assert result.request_count == 2
    assert result.successful_tile_count == 2
    assert requested_bboxes == expected_bboxes


def test_complete_response_geometry_is_transformed_to_output_crs():
    response_polygon = Polygon(
        [
            (200010.0, 500010.0),
            (200090.0, 500010.0),
            (200090.0, 500090.0),
            (200010.0, 500090.0),
            (200010.0, 500010.0),
        ]
    )
    output_aoi_geometry = transform_geometry(
        box(200000.0, 500000.0, 200100.0, 500100.0),
        "EPSG:5186",
        "EPSG:5179",
    )
    aoi = SpatialLayer(
        name="aoi",
        display_name="변환 범위",
        crs="EPSG:5179",
        geometry_type="Polygon",
        category="route",
        features=[SpatialFeature(output_aoi_geometry)],
    )
    expected = transform_geometry(
        MultiPolygon([response_polygon]),
        "EPSG:5186",
        "EPSG:5179",
    )

    result = _fetch_xml(
        _collection_xml(_feature_in_bounds("tbl_opn_eczm.transform", response_polygon.bounds)),
        aoi=aoi,
    )

    assert result.status == "success"
    assert result.feature_count == 1
    assert result.layer is not None
    assert result.layer.crs == "EPSG:5179"
    assert result.layer.features[0].geometry.equals_exact(expected, tolerance=1e-7)


def test_499_feature_members_are_a_non_saturated_successful_leaf():
    repeated = _feature_in_bounds(
        "tbl_opn_eczm.repeated",
        (200010.0, 500010.0, 200020.0, 500020.0),
    )

    result = _fetch_xml(_collection_xml(*([repeated] * 499)), tile_size_m=100)

    assert result.status == "success"
    assert result.feature_count == 1
    assert result.request_count == 1
    assert result.successful_tile_count == 1


def test_500_member_parent_is_discarded_and_children_merge_deterministically():
    requests: list[str] = []
    shared = _feature_in_bounds(
        "tbl_opn_eczm.shared",
        (200045.0, 500010.0, 200055.0, 500020.0),
    )
    left = _feature_in_bounds(
        "tbl_opn_eczm.left",
        (200010.0, 500010.0, 200020.0, 500020.0),
    )
    right = _feature_in_bounds(
        "tbl_opn_eczm.right",
        (200060.0, 500010.0, 200070.0, 500020.0),
    )
    responses = [
        _saturated_malformed_collection(),
        _collection_xml(shared, left),
        _collection_xml(shared, right),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.params["bbox"])
        return httpx.Response(200, content=responses[len(requests) - 1])

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = EcologyClient(
            SERVICE_KEY,
            tile_size_m=100,
            client=http_client,
        ).fetch_layer(DEFINITION, _aoi())

    assert requests == [
        "200000.0,500000.0,200100.0,500100.0",
        "200000.0,500000.0,200050.0,500100.0",
        "200050.0,500000.0,200100.0,500100.0",
    ]
    assert result.status == "success"
    assert result.initial_tile_count == 1
    assert result.request_count == 3
    assert result.successful_tile_count == 2
    assert result.failed_tile_count == 0
    assert result.feature_count == 3
    assert result.layer is not None
    assert [feature.feature_id for feature in result.layer.features] == [
        "tbl_opn_eczm.left",
        "tbl_opn_eczm.right",
        "tbl_opn_eczm.shared",
    ]
    assert all(not (feature.feature_id or "").startswith("parent.") for feature in result.layer.features)


def test_conflicting_fid_across_saturation_children_reports_output_tile_bbox():
    output_aoi = SpatialLayer(
        name="aoi",
        display_name="충돌 출력 좌표계 범위",
        crs="EPSG:5179",
        geometry_type="Polygon",
        category="route",
        features=[SpatialFeature(box(1_000_000.0, 2_000_000.0, 1_000_100.0, 2_000_100.0))],
    )
    fid = "tbl_opn_eczm.cross-child-conflict"
    first = _feature_in_bounds(
        fid,
        (200010.0, 500010.0, 200020.0, 500020.0),
    )
    conflicting = first.replace(
        "<open:eczm_grad>1</open:eczm_grad>",
        "<open:eczm_grad>2</open:eczm_grad>",
    )
    responses = [
        _saturated_malformed_collection(),
        _collection_xml(first),
        _collection_xml(conflicting),
    ]
    transport_calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal transport_calls
        response = responses[transport_calls]
        transport_calls += 1
        return httpx.Response(200, content=response)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = EcologyClient(
            SERVICE_KEY,
            tile_size_m=100,
            retries=0,
            client=http_client,
        ).fetch_layer(DEFINITION, output_aoi)

    conflicting_bbox = (
        1_000_050.0,
        2_000_000.0,
        1_000_100.0,
        2_000_100.0,
    )
    assert result.status == "failed"
    assert result.layer is None
    assert result.feature_count == 0
    assert transport_calls == 3
    assert result.initial_tile_count == 1
    assert result.request_count == 3
    assert result.successful_tile_count == 1
    assert result.failed_tile_count == 1
    assert result.failed_tile_bbox == conflicting_bbox
    assert "Ecology" in (result.error or "")
    assert fid in (result.error or "")
    assert str(conflicting_bbox) in (result.error or "")


def test_request_budget_is_layer_global_and_counts_only_real_attempts():
    transport_calls = 0
    responses = [_saturated_malformed_collection(), EMPTY_COLLECTION]

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal transport_calls
        response = responses[transport_calls]
        transport_calls += 1
        return httpx.Response(200, content=response)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = EcologyClient(
            SERVICE_KEY,
            tile_size_m=100,
            request_budget_minimum=2,
            request_budget_per_tile=0,
            request_budget_maximum=2,
            client=http_client,
        ).fetch_layer(DEFINITION, _aoi())

    assert result.status == "failed"
    assert result.layer is None
    assert result.feature_count == 0
    assert transport_calls == 2
    assert result.request_count == 2
    assert result.successful_tile_count == 1
    assert result.failed_tile_count == 1
    assert result.failed_tile_bbox == (200050.0, 500000.0, 200100.0, 500100.0)
    assert "budget=2" in (result.error or "")


def test_dense_ecology_layer_can_finish_beyond_the_initial_request_estimate():
    features = [(x * 100 + 40, y * 100 + 40, f"tbl_opn_eczm.{x}.{y}")
                for x in range(8) for y in range(8)]

    def handler(request):
        left, bottom, right, top = map(float, request.url.params["bbox"].split(","))
        matches = [_feature_in_bounds(fid, (x, y, x + 10, y + 10))
                   for x, y, fid in features if left < x < right and bottom < y < top]
        return httpx.Response(200, content=_collection_xml(*matches[:2]))

    definition = replace(DEFINITION, request_crs="EPSG:5186", response_crs="EPSG:5186")
    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = EcologyClient(SERVICE_KEY, page_size=2, retries=0, client=http_client).fetch_layer(
            definition, _aoi((0, 0, 800, 800)),
        )
    assert result.status == "success", result.error
    assert result.initial_tile_count == 1
    assert result.request_count == 127
    assert result.feature_count == result.successful_tile_count == 64


def test_retry_cannot_exceed_the_single_layer_request_budget():
    transport_calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal transport_calls
        transport_calls += 1
        raise httpx.ReadTimeout("retry me", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = EcologyClient(
            SERVICE_KEY,
            retries=3,
            backoff=0.25,
            request_budget_minimum=1,
            request_budget_per_tile=0,
            request_budget_maximum=1,
            client=http_client,
            sleep=sleeps.append,
        ).fetch_layer(DEFINITION, _aoi())

    assert result.status == "failed"
    assert transport_calls == 1
    assert result.request_count == 1
    assert result.failed_tile_bbox == (200000.0, 500000.0, 200100.0, 500100.0)
    assert sleeps == [0.25]
    assert "budget=1" in (result.error or "")


def test_saturated_tile_fails_atomically_at_maximum_split_depth():
    result = _fetch_xml(
        _saturated_malformed_collection(),
        tile_size_m=100,
        max_split_depth=0,
    )

    _assert_atomic_failure(result)
    assert "나눌 수 없습니다" in (result.error or "")
    assert "depth=0" in (result.error or "")


def test_saturated_tile_fails_atomically_at_minimum_tile_size():
    aoi = _aoi((200000.0, 500000.0, 200100.0, 500040.0))

    result = _fetch_xml(
        _saturated_malformed_collection(),
        aoi=aoi,
        tile_size_m=100,
        minimum_tile_size_m=50,
    )

    _assert_atomic_failure(result, (200000.0, 500000.0, 200100.0, 500040.0))
    assert "나눌 수 없습니다" in (result.error or "")


def test_terminal_second_child_failure_discards_partial_output_and_uses_output_bbox():
    output_aoi = SpatialLayer(
        name="aoi",
        display_name="출력 좌표계 범위",
        crs="EPSG:5179",
        geometry_type="Polygon",
        category="route",
        features=[SpatialFeature(box(1_000_000.0, 2_000_000.0, 1_000_100.0, 2_000_100.0))],
    )
    responses = [
        _saturated_malformed_collection(),
        _collection_xml(
            _feature_in_bounds(
                "tbl_opn_eczm.partial",
                (200010.0, 500010.0, 200020.0, 500020.0),
            )
        ),
        b"<not-xml",
    ]
    transport_calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal transport_calls
        response = responses[transport_calls]
        transport_calls += 1
        return httpx.Response(200, content=response)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        result = EcologyClient(
            SERVICE_KEY,
            tile_size_m=100,
            retries=0,
            client=http_client,
        ).fetch_layer(DEFINITION, output_aoi)

    assert result.status == "failed"
    assert result.layer is None
    assert result.feature_count == 0
    assert transport_calls == 3
    assert result.initial_tile_count == 1
    assert result.request_count == 3
    assert result.successful_tile_count == 1
    assert result.failed_tile_count == 1
    assert result.failed_tile_bbox == (
        1_000_050.0,
        2_000_000.0,
        1_000_100.0,
        2_000_100.0,
    )
