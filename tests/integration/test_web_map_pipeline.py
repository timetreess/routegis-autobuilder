import json
import re

import pytest

from routegis.app import project_service as module
from routegis.app.project_service import ProjectService
from routegis.cli import FixtureProvider
from routegis.errors import ExportError
from routegis.models import ProjectConfig


@pytest.fixture
def config(tmp_path, fixture_directory):
    return ProjectConfig(
        project_name="Web map", source_drawing=fixture_directory / "sample_route.dxf",
        source_layers=(), source_crs="EPSG:5186", output_crs="EPSG:5186",
        buffer_distance=1000, selected_layers=("cadastral", "building"),
        output_directory=tmp_path, project_type="non_linear",
    )


def test_build_publishes_standalone_web_map_with_only_selected_layers(config, fixture_directory):
    events = []
    result = ProjectService().build(
        config, provider=FixtureProvider(fixture_directory / "spatial_mock.json"),
        api_key="fixture-key", use_native_project=False,
        on_progress=lambda percent, text: events.append((percent, text)),
    )
    assert result.web_map_html == result.project_file.with_suffix(".html")
    assert result.web_map_html.is_file()
    text = result.web_map_html.read_text(encoding="utf-8")
    payload = json.loads(re.search(r'<script id="routegis-config" type="application/json">(.*?)</script>', text, re.S).group(1))
    layers = {item["id"]: item for item in payload["layers"]}
    assert set(layers) == {"linework", "route_buffer", "cadastral", "building"}
    assert layers["cadastral"]["count"] == 1
    assert layers["building"]["status"] == "failed" and layers["building"]["data"] is None
    assert layers["building"]["name"] == ProjectService().catalog.get("building").name
    assert "fixture-key" not in text and "sample_route.dxf" not in text
    manifest = json.loads(result.manifest.read_text(encoding="utf-8"))
    assert manifest["outputs"]["web_map"] == result.web_map_html.name
    assert result.web_map_html.name in result.report_html.read_text(encoding="utf-8")
    assert [percent for percent, _ in events] == sorted(percent for percent, _ in events)
    assert any("HTML" in message for _, message in events)
    assert result.web_map_html.parent == result.project_directory


def test_web_map_failure_preserves_previous_entire_project(config, fixture_directory, monkeypatch):
    provider = FixtureProvider(fixture_directory / "spatial_mock.json")
    service = ProjectService()
    previous = service.build(config, provider=provider, use_native_project=False)
    before = {path.name: path.read_bytes() for path in (previous.web_map_html, previous.manifest, previous.project_file)}

    def fail(*args, **kwargs):
        raise ExportError("web export failed")

    monkeypatch.setattr(module, "write_web_map", fail, raising=False)
    with pytest.raises(ExportError, match="web export failed"):
        service.build(config, provider=provider, use_native_project=False)
    assert before == {path.name: path.read_bytes() for path in (previous.web_map_html, previous.manifest, previous.project_file)}
    assert not list(config.output_directory.glob(".Web_map.build-*"))


def test_cancelling_web_export_removes_partial_html_without_losing_old_result(config, fixture_directory):
    provider = FixtureProvider(fixture_directory / "spatial_mock.json")
    service = ProjectService()
    previous = service.build(config, provider=provider, use_native_project=False)
    before = previous.web_map_html.read_bytes()

    class Cancelled(Exception):
        pass

    def cancel(percent, message):
        if percent >= 91 and "웹 지도" in message:
            raise Cancelled()

    with pytest.raises(Cancelled):
        service.build(config, provider=provider, use_native_project=False, on_progress=cancel)
    assert before == previous.web_map_html.read_bytes()
    assert not list(config.output_directory.glob(".Web_map.build-*"))
