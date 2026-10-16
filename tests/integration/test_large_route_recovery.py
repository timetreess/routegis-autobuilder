import re
from collections import Counter

import httpx
import pytest
from shapely.geometry import LineString, box

from routegis.gis import transform_geometry
from routegis.models import SpatialFeature, SpatialLayer
from routegis.providers.catalog import LayerCatalog
from routegis.providers.overpass.client import OverpassClient
from routegis.providers.vworld.client import VWorldClient


def _aoi():
    return SpatialLayer("aoi", "80 km corridor", "EPSG:5186", "Polygon", "route", [
        SpatialFeature(box(200000, 500000, 280000, 502000)),
    ])


@pytest.mark.parametrize("encoding", ["xml", "json"])
def test_vworld_http_200_pool_timeout_is_retried_then_only_failed_tile_is_split(encoding):
    requests = Counter()
    events = []
    sleeps = []
    definition = LayerCatalog().get("land_ownership")

    def handler(request):
        bounds = request.url.params["bbox"]
        requests[bounds] += 1
        left, _, right, _ = map(float, bounds.split(","))
        # Only the first 5 km tile is overloaded; its halves are healthy.
        if left < 127.001 and right - left > 0.04:
            message = "Unable to obtain connection: Timeout waiting for idle object"
            if encoding == "xml":
                return httpx.Response(200, text=f'<ServiceExceptionReport><ServiceException code="NoApplicableCode">{message}</ServiceException></ServiceExceptionReport>')
            return httpx.Response(200, json={"response": {"status": "ERROR", "error": {"code": "NoApplicableCode", "text": message}}})
        return httpx.Response(200, json={"type": "FeatureCollection", "features": []})

    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        result = VWorldClient("placeholder-key", client=transport, retries=1,
                              sleep=sleeps.append, on_progress=events.append).fetch_layer(definition, _aoi())
    assert result.status == "success", result.error
    assert result.initial_tile_count == 16
    assert result.successful_tile_count == 17
    assert result.request_count == sum(requests.values()) == 19
    assert sorted(requests.values()).count(2) == 1
    assert sleeps == [0.5]
    assert any("분할" in event for event in events)
    assert all("placeholder-key" not in event for event in events)


@pytest.mark.parametrize("failure", ["504", "timeout", "remark"])
def test_overpass_recovers_80km_by_splitting_only_busy_tiles_and_reuses_power_plan(failure):
    requests = Counter()
    events = []
    coordinates = list(transform_geometry(LineString([(201000, 501000), (202000, 501000)]),
                                          "EPSG:5186", "EPSG:4326").coords)
    geometry = [{"lon": x, "lat": y} for x, y in coordinates]
    elements = [
        {"type": "way", "id": 10, "nodes": [1, 2], "tags": {"power": "line"}, "geometry": geometry},
        *[{"type": "node", "id": index, "tags": {"power": "tower"}, **point}
          for index, point in enumerate(geometry, 1)],
    ]

    def handler(request):
        query = request.content.decode()
        requests[query] += 1
        south, west, north, east = map(float, re.search(r"\(([-\d.,]+)\)", query).group(1).split(","))
        if east - west > 0.13:
            if failure == "timeout":
                raise httpx.ReadTimeout("busy query", request=request)
            if failure == "remark":
                return httpx.Response(200, json={"elements": [], "remark": "runtime error: Query timed out"})
            return httpx.Response(504)
        return httpx.Response(200, json={"elements": elements})

    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        client = OverpassClient(client=transport, retries=0, on_progress=events.append)
        first = client.fetch_layer(LayerCatalog().get("power_line"), _aoi())
        second = client.fetch_layer(LayerCatalog().get("power_support"), _aoi())
    assert first.status == second.status == "success", (first.error, second.error)
    assert first.successful_tile_count > first.initial_tile_count
    assert second.successful_tile_count == first.successful_tile_count
    assert second.request_count == 0
    assert first.feature_count == 1 and second.feature_count == 2
    assert set(requests.values()) == {1}
    assert first.request_count == sum(requests.values())
    assert any("분할" in event for event in events)


def test_overpass_continuous_outage_is_bounded_and_never_returns_partial_success():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(504)

    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        result = OverpassClient(client=transport, backoff=0, request_budget_minimum=8,
                                request_budget_per_tile=0, request_budget_maximum=8).fetch_layer(
                                    LayerCatalog().get("river_area"), _aoi())
    assert result.status == "failed" and result.layer is None
    assert result.request_count == len(calls) == 8
    assert result.failed_tile_bbox is not None


def test_vworld_auth_error_is_not_retried_or_subdivided():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, text='<ServiceExceptionReport><ServiceException code="INVALID_KEY">bad key</ServiceException></ServiceExceptionReport>')

    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        result = VWorldClient("placeholder-key", client=transport).fetch_layer(
            LayerCatalog().get("land_ownership"), _aoi())
    assert result.status == "failed"
    assert result.request_count == len(calls) == 1
