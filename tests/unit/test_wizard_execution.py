"""Execute Wizard handlers with lightweight widgets when QGIS is unavailable."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from routegis.environment import load_data_go_kr_service_key, load_vworld_api_key
from routegis.errors import RouteGISError
from routegis.models import DownloadResult, PipelineResult, PipelineState, ProjectConfig
from routegis.providers.catalog import LayerCatalog
from routegis.security import redact_sensitive_text


def _handler(name, **globals):
    source = Path(__file__).resolve().parents[2] / "plugin" / "wizard.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    wizard = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    method = next(node for node in wizard.body if getattr(node, "name", "") == name)
    module = ast.Module(body=[method], type_ignores=[])
    namespace = {
        "ProjectConfig": ProjectConfig,
        "RouteGISError": RouteGISError,
        "redact_sensitive_text": redact_sensitive_text,
        "load_vworld_api_key": load_vworld_api_key,
        "load_data_go_kr_service_key": load_data_go_kr_service_key,
        "QApplication": Mock(),
        "QMessageBox": Mock(),
        **globals,
    }
    exec(compile(module, str(source), "exec"), namespace)
    return namespace[name]


def _field(value):
    return SimpleNamespace(text=lambda: value)


@pytest.fixture
def wizard(tmp_path):
    result = PipelineResult(
        state=PipelineState.COMPLETED_WITH_WARNINGS,
        project_directory=tmp_path / "result",
        report_html=tmp_path / "result" / "reports" / "processing_report.html",
    )
    service = SimpleNamespace(build=Mock(return_value=result), catalog=LayerCatalog())
    return SimpleNamespace(
        run_button=Mock(), open_button=Mock(), progress=Mock(), recent=Mock(),
        _save_credentials=Mock(), _open_project=Mock(), service=service,
        project_type="non_linear", project_name=_field("test"),
        vworld_api_key=_field("entered-vworld-key"),
        data_go_kr_service_key=_field("entered-ecology-key"),
        drawing=_field("drawing.dxf"), output_directory=_field(str(tmp_path)),
        _drawing_path=lambda: tmp_path / "drawing.dxf",
        _output_directory=lambda: tmp_path,
        _selected_layers=lambda: ("land_ownership", "ecology_nature"),
        _crs_code=lambda _: "EPSG:5186", source_crs=None, output_crs=None,
        buffer_distance=SimpleNamespace(value=lambda: 1000),
        settings=SimpleNamespace(snap_tolerance=1, chord_tolerance=1),
    )


@pytest.mark.parametrize("source", ["environment", "dotenv", "clean-pc"])
def test_build_uses_entered_keys_even_when_other_credentials_exist(wizard, tmp_path, monkeypatch, source):
    dotenv = tmp_path / ".env"
    for variable in ("VWORLD_API_KEY", "DATA_GO_KR_SERVICE_KEY"):
        monkeypatch.delenv(variable, raising=False)
        if source == "environment":
            monkeypatch.setenv(variable, "stale-key")
    if source == "dotenv":
        dotenv.write_text("VWORLD_API_KEY=stale-key\nDATA_GO_KR_SERVICE_KEY=stale-key\n", encoding="utf-8")

    _handler("_build", PLUGIN_DOTENV=dotenv)(wizard)

    kwargs = wizard.service.build.call_args.kwargs
    assert kwargs["api_key"] == "entered-vworld-key"
    assert kwargs["data_go_kr_service_key"] == "entered-ecology-key"


def test_catalog_uses_the_same_entered_key_as_build(wizard, tmp_path, monkeypatch):
    monkeypatch.setenv("VWORLD_API_KEY", "stale-key")
    wizard.load_vworld_button = Mock()
    wizard.layer_status = Mock()
    wizard._discover_vworld_catalog = Mock()
    wizard._finish_vworld_catalog = Mock()
    task = Mock()

    _handler("_load_vworld_catalog", PLUGIN_DOTENV=tmp_path / ".env", QgsTask=task,
             QgsApplication=Mock(), Qt=SimpleNamespace(WaitCursor=1))(wizard)

    assert task.fromFunction.call_args.kwargs["api_key"] == "entered-vworld-key"


def test_blank_fields_fall_back_to_environment(wizard, tmp_path, monkeypatch):
    wizard.vworld_api_key = _field("  ")
    wizard.data_go_kr_service_key = _field("")
    monkeypatch.setenv("VWORLD_API_KEY", "environment-vworld-key")
    monkeypatch.setenv("DATA_GO_KR_SERVICE_KEY", "environment-ecology-key")

    _handler("_build", PLUGIN_DOTENV=tmp_path / ".env")(wizard)

    kwargs = wizard.service.build.call_args.kwargs
    assert kwargs["api_key"] == "environment-vworld-key"
    assert kwargs["data_go_kr_service_key"] == "environment-ecology-key"


def test_result_shows_all_failed_causes_and_report_location_without_secrets(wizard, tmp_path):
    wizard.service.build.return_value.downloads = [
        DownloadResult("land_ownership", "failed", error="INVALID_KEY entered-vworld-key"),
        DownloadResult("ecology_nature", "failed", error="HTTP 403 SERVICE_KEY_IS_NOT_REGISTERED_ERROR entered-ecology-key"),
    ]

    _handler("_build", PLUGIN_DOTENV=tmp_path / ".env")(wizard)

    text = wizard.progress.setPlainText.call_args.args[0]
    assert "INVALID_KEY" in text
    assert "HTTP 403 SERVICE_KEY_IS_NOT_REGISTERED_ERROR" in text
    assert "토지소유공간정보" in text
    assert "모두 실패" in text
    assert str(wizard.service.build.return_value.report_html) in text
    assert "entered-vworld-key" not in text
    assert "entered-ecology-key" not in text


def test_zero_features_are_reported_as_success_not_download_failure(wizard, tmp_path):
    wizard.service.build.return_value.downloads = [DownloadResult("land_ownership", "success")]

    _handler("_build", PLUGIN_DOTENV=tmp_path / ".env")(wizard)

    text = wizard.progress.setPlainText.call_args.args[0]
    assert "해당없음" in text
    assert "모두 실패" not in text
