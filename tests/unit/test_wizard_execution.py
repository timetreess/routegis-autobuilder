"""Execute Wizard handlers with lightweight widgets when QGIS is unavailable."""

from __future__ import annotations

import ast
from pathlib import Path
from functools import partial
from time import monotonic
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from routegis.environment import load_data_go_kr_service_key, load_vworld_api_key
from routegis.errors import RouteGISError
from routegis.models import DownloadResult, PipelineResult, PipelineState, ProjectConfig
from routegis.providers.catalog import LayerCatalog
from routegis.providers.vworld.client import VWorldClient
from routegis.security import redact_sensitive_text


class _QueuedBuild:
    def __init__(self, service, config, **keys):
        self.service = service
        self.config = config
        self.keys = keys
        self.progress_message = Mock()
        self.completed = Mock()
        self.cancel = Mock()


class _QueuedAnalysis:
    def __init__(self, settings, drawing, region):
        self.drawing = drawing
        self.region = region
        self.progress_message = Mock()
        self.completed = Mock()
        self.cancel = Mock()


def _handler(name, **globals):
    source = Path(__file__).resolve().parents[2] / "plugin" / "wizard.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    wizard = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    method = next(node for node in wizard.body if isinstance(node, ast.FunctionDef) and node.name == name)
    method.decorator_list = []
    module = ast.Module(body=[method], type_ignores=[])
    namespace = {
        "ProjectConfig": ProjectConfig,
        "RouteGISError": RouteGISError,
        "redact_sensitive_text": redact_sensitive_text,
        "load_vworld_api_key": load_vworld_api_key,
        "load_data_go_kr_service_key": load_data_go_kr_service_key,
        "QApplication": Mock(),
        "QMessageBox": Mock(),
        "QgsApplication": Mock(),
        "ProjectBuildTask": _QueuedBuild,
        "DrawingAnalysisTask": _QueuedAnalysis,
        "Qt": SimpleNamespace(QueuedConnection=1),
        "QWizard": SimpleNamespace(CancelButton=1),
        "monotonic": monotonic,
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
    wizard = SimpleNamespace(
        run_button=Mock(), open_button=Mock(), progress=Mock(), recent=Mock(),
        _save_credentials=Mock(), _open_project=Mock(), service=service,
        project_type="non_linear", project_name=_field("test"),
        business_region=SimpleNamespace(currentText=lambda: "경기도"),
        vworld_api_key=_field("entered-vworld-key"),
        data_go_kr_service_key=_field("entered-ecology-key"),
        drawing=_field("drawing.dxf"), output_directory=_field(str(tmp_path)),
        _drawing_path=lambda: tmp_path / "drawing.dxf",
        _output_directory=lambda: tmp_path,
        _selected_layers=lambda: ("land_ownership", "ecology_nature"),
        _crs_code=lambda _: "EPSG:5186", source_crs=None, output_crs=None,
        buffer_distance=SimpleNamespace(value=lambda: 1000),
        settings=SimpleNamespace(snap_tolerance=1, chord_tolerance=1),
        _build_task=None, _vworld_task=None, _build_timer=Mock(),
        progress_bar=Mock(), build_stage=Mock(), build_elapsed=Mock(), button=Mock(),
        _set_build_running=Mock(), _append_build_log=Mock(), _on_build_progress=Mock(),
        _preview_timer=Mock(), _preview=Mock(), crs_recommendations=Mock(),
        coordinate_report=Mock(), recommendation_status=Mock(), preview_status=Mock(),
        _analysis_task=None, _analysis_timer=Mock(), _preview_load_timer=Mock(),
        _preview_iterator=None, _set_analysis_running=Mock(), _invalidate_analysis=Mock(),
        _on_analysis_progress=Mock(), _finish_analysis=Mock(),
        _append_analysis_log=Mock(), analysis_log=Mock(), analysis_progress=Mock(),
        analysis_stage=Mock(), analysis_elapsed=Mock(),
    )
    wizard._finish_build = partial(_handler("_finish_build"), wizard)
    wizard._update_build_elapsed = partial(_handler("_update_build_elapsed"), wizard)
    return wizard


def test_analysis_is_explicit_and_never_reads_dxf_on_the_ui_thread(wizard):
    wizard.service.analyze_drawing = Mock()
    _handler("_analyze")(wizard)
    wizard.service.analyze_drawing.assert_not_called()
    assert wizard._analysis_task.drawing == wizard._drawing_path()
    assert wizard._analysis_task.region == "경기도"
    wizard._analysis_timer.start.assert_called_once()
    wizard._set_analysis_running.assert_called_with(True)


def test_analysis_cannot_be_queued_twice(wizard):
    analyze = _handler("_analyze")
    analyze(wizard)
    first = wizard._analysis_task
    analyze(wizard)
    assert wizard._analysis_task is first


def test_analysis_progress_ignores_old_worker_and_redacts_keys(wizard):
    old = _QueuedAnalysis(wizard.settings, wizard._drawing_path(), "경기도")
    current = _QueuedAnalysis(wizard.settings, wizard._drawing_path(), "경기도")
    wizard._analysis_task = current
    wizard.sender = lambda: old
    progress = _handler("_on_analysis_progress")
    progress(wizard, 40, "old drawing")
    wizard.analysis_progress.setValue.assert_not_called()
    wizard.sender = lambda: current
    progress(wizard, 60, "scan entered-vworld-key")
    wizard.analysis_progress.setValue.assert_called_once_with(60)
    assert "entered-vworld-key" not in wizard.analysis_stage.setText.call_args.args[0]


def test_next_cannot_advance_while_native_preview_is_still_loading(wizard):
    wizard._preview_iterator = iter([1])
    assert _handler("validateCurrentPage")(wizard) is False


def test_obsolete_analysis_completion_cannot_replace_new_drawing(wizard):
    old = _QueuedAnalysis(wizard.settings, wizard._drawing_path(), "경기도")
    current = _QueuedAnalysis(wizard.settings, wizard._drawing_path(), "경기도")
    wizard._analysis_task = current
    wizard.sender = lambda: old
    _handler("_finish_analysis")(wizard, None, {"old": "result"})
    assert wizard._analysis_task is current
    wizard._set_analysis_running.assert_not_called()


@pytest.mark.parametrize("cancelled", [False, True])
def test_failed_or_cancelled_analysis_restores_controls_without_publishing(wizard, cancelled):
    task = _QueuedAnalysis(wizard.settings, wizard._drawing_path(), "경기도")
    wizard._analysis_task = task
    wizard.sender = lambda: task
    wizard._update_analysis_elapsed = Mock()
    wizard.project_type = None
    _handler("_finish_analysis")(wizard, None if cancelled else "read failed entered-vworld-key", None, cancelled)
    assert wizard._analysis_task is None and wizard.project_type is None
    wizard._analysis_timer.stop.assert_called_once()
    wizard._set_analysis_running.assert_called_once_with(False)
    assert "entered-vworld-key" not in wizard.analysis_stage.setText.call_args.args[0]


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

    kwargs = wizard._build_task.keys
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


def test_catalog_refresh_preserves_selection_edits_made_while_loading(wizard, tmp_path):
    wizard.load_vworld_button = Mock()
    wizard.layer_status = Mock()
    wizard._discover_vworld_catalog = Mock()
    wizard._finish_vworld_catalog = partial(_handler("_finish_vworld_catalog"), wizard)
    wizard._render_layer_list = Mock()

    _handler("_load_vworld_catalog", PLUGIN_DOTENV=tmp_path / ".env", QgsTask=Mock(),
             Qt=SimpleNamespace(WaitCursor=1))(wizard)

    wizard._selected_layers = lambda: ("power_line",)
    wizard._finish_vworld_catalog(None, {"definitions": []})

    wizard._render_layer_list.assert_called_once_with({"power_line"})
    assert "선택 1개" in wizard.layer_status.setText.call_args.args[0]
    assert wizard._vworld_task is None
    wizard.load_vworld_button.setEnabled.assert_called_with(True)


def test_blank_fields_fall_back_to_environment(wizard, tmp_path, monkeypatch):
    wizard.vworld_api_key = _field("  ")
    wizard.data_go_kr_service_key = _field("")
    monkeypatch.setenv("VWORLD_API_KEY", "environment-vworld-key")
    monkeypatch.setenv("DATA_GO_KR_SERVICE_KEY", "environment-ecology-key")

    _handler("_build", PLUGIN_DOTENV=tmp_path / ".env")(wizard)

    kwargs = wizard._build_task.keys
    assert kwargs["api_key"] == "environment-vworld-key"
    assert kwargs["data_go_kr_service_key"] == "environment-ecology-key"


def test_result_shows_all_failed_causes_and_report_location_without_secrets(wizard, tmp_path):
    wizard.service.build.return_value.downloads = [
        DownloadResult("land_ownership", "failed", error="INVALID_KEY entered-vworld-key"),
        DownloadResult("ecology_nature", "failed", error="HTTP 403 SERVICE_KEY_IS_NOT_REGISTERED_ERROR entered-ecology-key"),
    ]

    _handler("_build", PLUGIN_DOTENV=tmp_path / ".env")(wizard)
    wizard._finish_build(None, wizard.service.build.return_value)

    text = wizard._append_build_log.call_args.args[0]
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
    wizard._finish_build(None, wizard.service.build.return_value)

    text = wizard._append_build_log.call_args.args[0]
    assert "해당없음" in text
    assert "모두 실패" not in text


def test_build_does_not_execute_pipeline_on_the_ui_thread(wizard, tmp_path):
    _handler("_build", PLUGIN_DOTENV=tmp_path / ".env")(wizard)

    wizard.service.build.assert_not_called()
    assert wizard._build_task is not None
    assert wizard._build_task.config.project_type == "non_linear"
    assert wizard._build_task.config.source_layers == ()


def test_live_progress_updates_stage_and_appends_instead_of_replacing_log(wizard, tmp_path):
    _handler("_build", PLUGIN_DOTENV=tmp_path / ".env")(wizard)
    _handler("_on_build_progress")(wizard, 42, "생태자연도 수집 중")

    wizard.progress_bar.setValue.assert_called_with(42)
    wizard.build_stage.setText.assert_called_with("생태자연도 수집 중")
    wizard._append_build_log.assert_called_with("생태자연도 수집 중")
    wizard.progress.setPlainText.assert_not_called()


def test_failed_build_restores_controls_and_keeps_last_progress(wizard, tmp_path):
    _handler("_build", PLUGIN_DOTENV=tmp_path / ".env")(wizard)
    _handler("_on_build_progress")(wizard, 22, "분석범위 생성 중")
    wizard._finish_build("Buffer error entered-vworld-key")

    assert wizard._build_task is None and wizard.result is None
    wizard._build_timer.stop.assert_called_once()
    wizard._set_build_running.assert_called_with(False)
    wizard.progress_bar.setValue.assert_called_with(22)
    assert "Buffer error" in wizard.build_stage.setText.call_args.args[0]
    assert "entered-vworld-key" not in wizard.build_stage.setText.call_args.args[0]


def test_cancel_waits_for_worker_then_restores_controls_without_opening_result(wizard, tmp_path):
    _handler("_build", PLUGIN_DOTENV=tmp_path / ".env")(wizard)
    task = wizard._build_task
    _handler("_cancel_build")(wizard)
    task.cancel.assert_called_once()
    assert wizard._build_task is task
    wizard._finish_build(None, None, True)
    assert wizard._build_task is None and wizard.result is None
    wizard._open_project.assert_not_called()
    wizard._set_build_running.assert_called_with(False)


def test_elapsed_time_indicates_waiting_without_inventing_percentage(wizard, tmp_path):
    _handler("_build", PLUGIN_DOTENV=tmp_path / ".env")(wizard)
    wizard._last_build_update = monotonic() - 40
    wizard._update_build_elapsed()
    text = wizard.build_elapsed.setText.call_args.args[0]
    assert "40초 전" in text and "기다리고" in text
    wizard.progress_bar.setValue.assert_called_once_with(0)


def test_blank_output_directory_is_rejected(wizard):
    wizard.output_directory = _field("   ")

    with pytest.raises(RouteGISError, match="저장 위치"):
        _handler("_output_directory", Path=Path)(wizard)


def test_changing_drawing_clears_previous_analysis(wizard):
    wizard.candidate_table = Mock()
    wizard.cad_status = Mock()

    _handler("_invalidate_analysis")(wizard)

    assert wizard.project_type is None
    wizard.candidate_table.setRowCount.assert_called_once_with(0)
    wizard._preview.clear_drawing.assert_called_once()
    wizard._preview_timer.stop.assert_called_once()
    assert wizard.service.last_coordinate_report is None
    assert wizard.service.last_cad_lines == {}


def test_credentials_are_trimmed_and_blank_fields_remove_saved_values(wizard):
    stored = {}
    wizard.recent = SimpleNamespace(
        setValue=stored.__setitem__, remove=lambda key: stored.pop(key, None), sync=Mock(),
    )
    wizard.vworld_api_key = _field(" entered-vworld-key ")
    save = _handler("_save_credentials")

    save(wizard)
    assert stored == {
        "vworld_api_key": "entered-vworld-key",
        "data_go_kr_service_key": "entered-ecology-key",
    }

    wizard.vworld_api_key = _field(" ")
    wizard.data_go_kr_service_key = _field("")
    save(wizard)
    assert stored == {}
    assert wizard.recent.sync.call_count == 2


@pytest.mark.parametrize("failure", [False, True])
def test_catalog_worker_closes_client_and_redacts_failure(wizard, failure):
    client = Mock(spec=VWorldClient)
    definitions = wizard.service.catalog.all()
    client.discover_layers.return_value = definitions
    if failure:
        client.discover_layers.side_effect = RuntimeError("catalog unavailable entered-vworld-key")
    service = SimpleNamespace(_provider=Mock(return_value=client))
    task = SimpleNamespace(isCanceled=lambda: False)

    result = _handler(
        "_discover_vworld_catalog", ProjectService=lambda _: service, VWorldClient=VWorldClient,
    )(task, wizard.settings, "entered-vworld-key")

    client.close.assert_called_once()
    if failure:
        assert "catalog unavailable" in result["error"]
        assert "entered-vworld-key" not in result["error"]
    else:
        assert result["definitions"] == definitions
