from dataclasses import replace
import json
from threading import Barrier, Event, get_ident

import pytest

from routegis.app.project_service import ProjectService
from routegis.cli import FixtureProvider
from routegis.models import DownloadResult, ProjectConfig, SpatialLayer
from routegis.app import project_service as service_module
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


def test_parallel_provider_progress_is_serialized_and_manifest_keeps_selection_order(config, monkeypatch):
    selected = ("land_ownership", "ecology_nature", "power_line",
                "heritage_protection", "power_support", "river_area")
    config = replace(config, selected_layers=selected)
    coordinator, barrier, allow_land = get_ident(), Barrier(3), Event()
    clients, events, finish_order = {}, [], []
    service = ProjectService()

    class Client:
        def __init__(self, name, progress):
            self.name, self.progress, self.thread = name, progress, get_ident()
            self.first, self.closed = True, False

        def fetch_layer(self, definition, aoi):
            assert get_ident() == self.thread != coordinator
            if self.first:
                self.first = False
                barrier.wait(timeout=3)
            if definition.id == "land_ownership":
                assert allow_land.wait(timeout=3)
            self.progress(f"phase:{definition.id} fixture-key")
            return DownloadResult(layer_id=definition.id, status="success", request_count=7,
                                  layer=SpatialLayer(definition.id, definition.name, aoi.crs,
                                                     definition.geometry_type, definition.category,
                                                     source=definition.provider))

        def close(self):
            assert get_ident() == self.thread
            self.closed = True

    def factory(name, **kwargs):
        client = Client(name, kwargs["on_progress"])
        clients[name] = client
        return client

    def progress(percent, message):
        assert get_ident() == coordinator
        events.append((percent, message))
        if "성공 ·" in message:
            finish_order.append(message)
        if "생태자연도" in message and "성공 ·" in message:
            allow_land.set()

    export = service_module.export_layers

    def checked_export(*args, **kwargs):
        assert get_ident() == coordinator
        assert len(clients) == 3 and all(client.closed for client in clients.values())
        return export(*args, **kwargs)

    monkeypatch.setattr(service_module, "export_layers", checked_export)
    monkeypatch.setattr(service, "_provider", factory)
    result = service.build(config, api_key="fixture-key", on_progress=progress, use_native_project=False)
    assert [d.layer_id for d in result.downloads] == list(selected)
    manifest = json.loads(result.manifest.read_text(encoding="utf-8"))
    assert [d["id"] for d in manifest["layers"]] == list(selected)
    assert "토지소유" not in finish_order[0]
    assert [percent for percent, _ in events] == sorted(percent for percent, _ in events)
    text = (result.project_directory / "logs/app.log").read_text(encoding="utf-8")
    assert "fixture-key" not in text
    for definition in service.catalog.select(selected):
        matching = [message for _, message in events if f"phase:{definition.id} " in message]
        assert len(matching) == 1 and definition.name in matching[0]
    assert "API 요청 7회" in text


def test_cancelling_parallel_downloads_closes_clients_before_removing_staging(config, fixture_directory, monkeypatch):
    config = replace(config, selected_layers=("land_ownership", "heritage_protection", "ecology_nature", "power_line"))
    service = ProjectService()
    previous = service.build(config, provider=FixtureProvider(fixture_directory / "spatial_mock.json"))
    manifest = previous.manifest.read_bytes()
    barrier, closed = Barrier(3), []

    class Cancelled(Exception):
        pass

    class Client:
        def __init__(self, progress, check):
            self.progress, self.check = progress, check

        def fetch_layer(self, definition, aoi):
            barrier.wait(timeout=3)
            self.progress("request started")
            Event().wait(timeout=0.2)
            self.check()
            pytest.fail("download continued after cancellation")

        def close(self):
            assert list(config.output_directory.glob(".Progress.build-*"))
            closed.append(True)

    def factory(name, **kwargs):
        return Client(kwargs["on_progress"], kwargs["check_cancelled"])

    def progress(percent, message):
        if "request started" in message:
            raise Cancelled()

    monkeypatch.setattr(service, "_provider", factory)
    with pytest.raises(Cancelled):
        service.build(config, on_progress=progress, use_native_project=False)
    assert len(closed) == 3
    assert previous.manifest.read_bytes() == manifest
    assert not list(config.output_directory.glob(".Progress.build-*"))
