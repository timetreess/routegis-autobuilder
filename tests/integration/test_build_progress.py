from dataclasses import replace

import pytest

from routegis.app.project_service import ProjectService
from routegis.cli import FixtureProvider
from routegis.models import ProjectConfig
from routegis.qgis import project as project_module


@pytest.fixture
def config(tmp_path, fixture_directory):
    return ProjectConfig(
        project_name="Progress", source_drawing=fixture_directory / "sample_route.dxf",
        source_layers=(), source_crs="EPSG:5186", output_crs="EPSG:5186",
        buffer_distance=1000, selected_layers=("cadastral", "building"),
        output_directory=tmp_path, project_type="non_linear",
    )


def test_progress_arrives_before_fetch_and_reports_partial_failure(config, fixture_directory, monkeypatch):
    events = []
    fixture = FixtureProvider(fixture_directory / "spatial_mock.json")

    class Provider:
        def fetch_layer(self, definition, aoi):
            assert definition.name in events[-1][1]
            assert "수집 중" in events[-1][1]
            return fixture.fetch_layer(definition, aoi)

    def forbidden_native(*args, **kwargs):
        pytest.fail("Background builds must not create QGIS objects")

    monkeypatch.setattr(project_module, "_native_project", forbidden_native)
    result = ProjectService().build(
        config, api_key="fixture-key", provider=Provider(),
        on_progress=lambda percent, message: events.append((percent, message)),
        use_native_project=False,
    )

    percentages = [percent for percent, _ in events]
    assert percentages == sorted(percentages)
    assert percentages[0] == 0 and percentages[-1] == 99
    messages = "\n".join(message for _, message in events)
    for expected in ("도면", "좌표계", "분석범위", "GeoPackage", "QGIS", "보고서", "mock timeout", "실패"):
        assert expected in messages
    assert "fixture-key" not in messages
    assert result.project_file.is_file()
    assert "mock timeout" in (result.project_directory / "logs" / "app.log").read_text(encoding="utf-8")


def test_cancelling_at_progress_boundary_removes_staging_and_keeps_existing_project(config, fixture_directory):
    service = ProjectService()
    fixture = FixtureProvider(fixture_directory / "spatial_mock.json")
    previous = service.build(config, provider=fixture)
    original_manifest = previous.manifest.read_bytes()

    class Cancelled(Exception):
        pass

    def cancel_after_download(percent, message):
        if percent >= 75:
            raise Cancelled()

    with pytest.raises(Cancelled):
        service.build(config, provider=fixture, on_progress=cancel_after_download)

    assert previous.manifest.read_bytes() == original_manifest
    assert not list(config.output_directory.glob(".Progress.build-*"))


def test_progress_also_covers_build_without_provider_layers(config):
    events = []
    result = ProjectService().build(
        replace(config, selected_layers=()),
        on_progress=lambda percent, message: events.append((percent, message)),
    )
    assert result.project_file.is_file()
    assert events[-1][0] == 99
