import json
from html.parser import HTMLParser
from urllib.parse import quote

import pytest
from shapely.geometry import LineString, Polygon, shape

from routegis.gis.crs import transform_geometry
from routegis.models import DownloadResult, SpatialFeature, SpatialLayer
from routegis.web_map import write_web_map


class JSONScripts(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.scripts = {}
        self.current = None
        self.external_assets = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script":
            self.current = attrs.get("id") if attrs.get("type") == "application/json" else None
            if self.current:
                self.scripts[self.current] = ""
        if (tag == "script" and attrs.get("src")) or (tag == "link" and attrs.get("href")):
            self.external_assets.append(attrs)

    def handle_data(self, data):
        if self.current:
            self.scripts[self.current] += data

    def handle_endtag(self, tag):
        if tag == "script":
            self.current = None

    @property
    def config(self):
        return json.loads(self.scripts["routegis-config"])

    def collection(self, layer_id):
        entry = next(item for item in self.config["layers"] if item["id"] == layer_id)
        return json.loads(self.scripts[entry["data"]])


@pytest.fixture
def layers():
    line = LineString([(200000, 500000), (201000, 501000)])
    polygon = Polygon([(199000, 499000), (202000, 499000), (202000, 502000), (199000, 502000)])
    return [
        SpatialLayer("linework", "선형", "EPSG:5186", "LineString", "route", [SpatialFeature(line, {"dxf_layer": "ROAD"}, "line-1")]),
        SpatialLayer("route_buffer", "사업구간 주변 범위", "EPSG:5186", "Polygon", "route", [SpatialFeature(polygon)]),
        SpatialLayer("land_ownership", "토지소유공간정보", "EPSG:5186", "MultiPolygon", "land", [SpatialFeature(polygon, {"lbl": "개인"}, "parcel-1")]),
        SpatialLayer("original_cad", "원본 CAD", "EPSG:5186", "LineString", "source", [SpatialFeature(line, {"secret_raw_cad": True})]),
    ]


def test_standalone_map_embeds_data_and_viewer_without_original_cad(tmp_path, layers):
    path = tmp_path / "사업 지도.html"
    originals = [feature.geometry.wkb for layer in layers for feature in layer.features]
    assert write_web_map(path, "사업 지도", layers) == path
    text = path.read_text(encoding="utf-8")
    parsed = JSONScripts(text)
    assert parsed.external_assets == []
    assert "url(images/" not in text and "sourceMappingURL=" not in text
    assert "data:image/png;base64," in text
    assert "Leaflet" in text and "Redistribution and use" in text
    assert {item["id"] for item in parsed.config["layers"]} == {"linework", "route_buffer", "land_ownership"}
    assert "secret_raw_cad" not in text
    assert set(tmp_path.iterdir()) == {path}
    collection = parsed.collection("linework")
    feature = collection["features"][0]
    expected = transform_geometry(layers[0].features[0].geometry, "EPSG:5186", "EPSG:4326")
    assert shape(feature["geometry"]).equals_exact(expected, 1e-10)
    assert feature["id"] == "line-1" and feature["properties"] == {"dxf_layer": "ROAD"}
    assert originals == [feature.geometry.wkb for layer in layers for feature in layer.features]
    assert all(layer.crs == "EPSG:5186" for layer in layers)


def test_properties_and_names_cannot_escape_script_or_leak_credentials(tmp_path, layers):
    attack = '</script><img src=x onerror="window.pwned=true">&\u2028'
    secret = "portal+/=secret"
    layers[0].features[0].properties = {"name": attack, "credential": secret, "encoded": quote(secret, safe="")}
    layers[0].display_name = attack
    path = write_web_map(tmp_path / "safe.html", attack, layers, secrets=(secret,))
    text = path.read_text(encoding="utf-8")
    parsed = JSONScripts(text)
    assert attack not in text and "\\u003c/script" in text
    assert secret not in text and quote(secret, safe="") not in text
    assert parsed.config["title"] == attack
    assert parsed.collection("linework")["features"][0]["properties"]["name"] == attack
    assert parsed.collection("linework")["features"][0]["properties"]["credential"] == "***"


def test_failures_and_empty_layers_are_distinct_with_attribution(tmp_path, layers):
    layers.append(SpatialLayer("river_area", "하천", "EPSG:5186", "MultiPolygon", "water"))
    metadata = {
        "land_ownership": {"name": "토지소유공간정보", "publisher": "VWorld", "attribution": "VWorld", "license_name": "이용조건", "license_url": "https://www.vworld.kr/"},
        "ecology_nature": {"name": "생태자연도", "publisher": "국립생태원", "license_name": "이용조건"},
    }
    results = [DownloadResult("river_area", "success"), DownloadResult("ecology_nature", "failed", error="HTTP 503")]
    parsed = JSONScripts(write_web_map(tmp_path / "status.html", "상태", layers,
                                      layer_metadata=metadata, downloads=results).read_text(encoding="utf-8"))
    statuses = {entry["id"]: entry for entry in parsed.config["layers"]}
    assert statuses["ecology_nature"]["status"] == "failed"
    assert statuses["ecology_nature"]["error"] == "HTTP 503"
    assert statuses["ecology_nature"]["data"] is None
    assert statuses["river_area"]["status"] == "success" and statuses["river_area"]["count"] == 0
    assert parsed.collection("river_area")["features"] == []
    assert statuses["land_ownership"]["metadata"]["publisher"] == "VWorld"
    assert statuses["land_ownership"]["style"]["field"] == "lbl"
    assert statuses["land_ownership"]["style"]["categories"][0]["value"] == "개인"


def test_cancelled_export_preserves_existing_map_and_cleans_only_its_temp_file(tmp_path, layers):
    path = tmp_path / "map.html"
    path.write_text("previous map", encoding="utf-8")

    class Cancelled(Exception):
        pass

    def cancel(percent, message):
        if percent >= 50:
            raise Cancelled()

    with pytest.raises(Cancelled):
        write_web_map(path, "cancel", layers, on_progress=cancel)
    assert path.read_text(encoding="utf-8") == "previous map"
    assert set(tmp_path.iterdir()) == {path}


def test_progress_is_monotonic_and_records_feature_conversion(tmp_path, layers):
    events = []
    write_web_map(tmp_path / "progress.html", "진행", layers,
                  on_progress=lambda percent, message: events.append((percent, message)))
    percentages = [percent for percent, _ in events]
    assert percentages == sorted(percentages)
    assert percentages[0] == 0 and percentages[-1] == 100
    assert any("선형" in message for _, message in events)
