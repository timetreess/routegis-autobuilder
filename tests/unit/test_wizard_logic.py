"""Wizard decisions that need no QGIS (widgets are covered by tests/manual/qgis_smoke.py)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from routegis.app.wizard_logic import (
    build_result_lines,
    cad_warning_suffix,
    catalog_discovery_outcome,
    coordinate_report_text,
    credential_log_line,
    order_selected_layers,
    parse_crs_code,
    recommendation_status_text,
    resolve_credential,
    stored_credentials,
    validated_output_directory,
)
from routegis.config import load_profile
from routegis.environment import load_data_go_kr_service_key, load_vworld_api_key
from routegis.errors import RouteGISError
from routegis.models import DownloadResult, PipelineResult, PipelineState, ValidationIssue
from routegis.providers.catalog import LayerCatalog

ENTERED_VWORLD = "entered-vworld-key"
ENTERED_ECOLOGY = "entered-ecology-key"


@pytest.fixture
def clean_environment(monkeypatch):
    for variable in ("VWORLD_API_KEY", "DATA_GO_KR_SERVICE_KEY"):
        monkeypatch.delenv(variable, raising=False)


def _resolve(entered_vworld, entered_ecology, dotenv: Path):
    return (
        resolve_credential(entered_vworld, lambda: load_vworld_api_key(dotenv)),
        resolve_credential(entered_ecology, lambda: load_data_go_kr_service_key(dotenv)),
    )


@pytest.mark.parametrize("source", ["environment", "dotenv", "clean-pc"])
def test_entered_keys_win_even_when_other_credentials_exist(tmp_path, monkeypatch, clean_environment, source):
    dotenv = tmp_path / ".env"
    if source == "environment":
        monkeypatch.setenv("VWORLD_API_KEY", "stale-key")
        monkeypatch.setenv("DATA_GO_KR_SERVICE_KEY", "stale-key")
    if source == "dotenv":
        dotenv.write_text("VWORLD_API_KEY=stale-key\nDATA_GO_KR_SERVICE_KEY=stale-key\n", encoding="utf-8")

    vworld, ecology = _resolve(f" {ENTERED_VWORLD} ", ENTERED_ECOLOGY, dotenv)

    assert (vworld.value, ecology.value) == (ENTERED_VWORLD, ENTERED_ECOLOGY)
    assert credential_log_line("VWorld", vworld) == "VWorld 인증키: 입력란(저장값 포함)"


def test_blank_fields_fall_back_to_environment_then_dotenv(tmp_path, monkeypatch, clean_environment):
    dotenv = tmp_path / ".env"
    dotenv.write_text("VWORLD_API_KEY=dotenv-vworld\nDATA_GO_KR_SERVICE_KEY=dotenv-ecology\n", encoding="utf-8")
    vworld, ecology = _resolve("  ", "", dotenv)
    assert (vworld.value, ecology.value) == ("dotenv-vworld", "dotenv-ecology")
    assert credential_log_line("VWorld", vworld) == "VWorld 인증키: 환경변수 / 플러그인 .env"

    monkeypatch.setenv("VWORLD_API_KEY", "environment-vworld-key")
    monkeypatch.setenv("DATA_GO_KR_SERVICE_KEY", "environment-ecology-key")
    vworld, ecology = _resolve("  ", "", dotenv)
    assert (vworld.value, ecology.value) == ("environment-vworld-key", "environment-ecology-key")


def test_missing_key_is_labelled_unset(tmp_path, clean_environment):
    vworld, _ = _resolve("", "", tmp_path / ".env")
    assert vworld.value == ""
    assert credential_log_line("VWorld", vworld) == "VWorld 인증키: 미설정"


def test_credentials_are_trimmed_and_blank_values_mean_remove():
    assert stored_credentials(f" {ENTERED_VWORLD} ", ENTERED_ECOLOGY) == {
        "vworld_api_key": ENTERED_VWORLD, "data_go_kr_service_key": ENTERED_ECOLOGY,
    }
    assert stored_credentials(" ", "") == {"vworld_api_key": "", "data_go_kr_service_key": ""}


def test_blank_output_directory_is_rejected():
    with pytest.raises(RouteGISError, match="저장 위치"):
        validated_output_directory("   ")
    assert validated_output_directory(" /tmp/out ") == Path("/tmp/out")


def test_selected_layers_put_default_profile_first_then_list_order():
    defaults = load_profile()
    extra = ("zz_extra_b", "zz_extra_a")
    checked = (*extra, *reversed(defaults))
    assert order_selected_layers(checked, defaults) == (*defaults, *extra)
    assert order_selected_layers((), defaults) == ()
    assert order_selected_layers(("only_extra",), defaults) == ("only_extra",)
    assert order_selected_layers(defaults[1:], defaults) == defaults[1:]


@pytest.mark.parametrize(
    "text, expected",
    [("EPSG:5186 — 중부원점", "EPSG:5186"), (" epsg:5174 ", "EPSG:5174"), ("EPSG:4326", "EPSG:4326")],
)
def test_crs_code_is_parsed_from_selector_text(text, expected):
    assert parse_crs_code(text) == expected


def test_coordinate_report_and_recommendation_texts():
    report = SimpleNamespace(
        bounds=(200000.0, 489000.0, 201000.0, 490000.0), center=(200500.0, 489500.0),
        insunits=6, unit_name="Meters",
    )
    text = coordinate_report_text(report)
    assert "X 200,000.000 ~ 201,000.000" in text and "$INSUNITS=6 (Meters)" in text
    assert "추천 후보 2개" in recommendation_status_text(2, 6)
    assert "미터로 기록되어 있지 않습니다" not in recommendation_status_text(2, 0)
    assert "후보가 없습니다" in recommendation_status_text(0, 6)
    assert "미터로 기록되어 있지 않습니다" in recommendation_status_text(1, 4)


def test_cad_warning_suffix_lists_each_issue():
    assert cad_warning_suffix(()) == ""
    issue = ValidationIssue("warning", "CAD_SKIPPED", "길이 0", layer="AUX")
    assert cad_warning_suffix((issue,)) == "\n경고:\n- AUX: 길이 0"


def _result(tmp_path: Path, **overrides) -> PipelineResult:
    values = dict(
        state=PipelineState.COMPLETED_WITH_WARNINGS,
        project_directory=tmp_path / "result",
        report_html=tmp_path / "result" / "reports" / "processing_report.html",
        geopackage=tmp_path / "result" / "p.gpkg",
        project_file=tmp_path / "result" / "p.qgz",
    )
    values.update(overrides)
    return PipelineResult(**values)


def _with_shapefile(result: PipelineResult) -> PipelineResult:
    folder = result.project_directory / "shp" / "category"
    folder.mkdir(parents=True)
    (folder / "layer.shp").write_bytes(b"")
    return result


def _text(result: PipelineResult, secrets=()) -> tuple[str, int]:
    lines, failed = build_result_lines(result, LayerCatalog().get, secrets)
    return "\n".join(lines), failed


def test_failed_downloads_show_all_causes_and_report_location_without_secrets(tmp_path):
    result = _result(tmp_path, downloads=[
        DownloadResult("land_ownership", "failed", error=f"INVALID_KEY {ENTERED_VWORLD}"),
        DownloadResult(
            "ecology_nature", "failed",
            error=f"HTTP 403 SERVICE_KEY_IS_NOT_REGISTERED_ERROR {ENTERED_ECOLOGY}",
        ),
    ])
    text, failed = _text(result, (ENTERED_VWORLD, ENTERED_ECOLOGY))
    assert failed == 2
    assert "INVALID_KEY" in text and "HTTP 403 SERVICE_KEY_IS_NOT_REGISTERED_ERROR" in text
    assert "토지소유공간정보" in text and "모두 실패" in text
    assert str(result.report_html) in text
    assert ENTERED_VWORLD not in text and ENTERED_ECOLOGY not in text


def test_partial_failure_is_counted_and_blank_error_gets_default_cause(tmp_path):
    result = _result(tmp_path, downloads=[
        DownloadResult("land_ownership", "success", feature_count=3),
        DownloadResult("ecology_nature", "failed"),
    ])
    text, failed = _text(result)
    assert failed == 1 and "2개 중 1개가 실패" in text
    assert "성공 · 3개" in text and "상세 오류를 반환하지 않았습니다" in text


def test_zero_features_are_success_not_failure(tmp_path):
    text, failed = _text(_result(tmp_path, downloads=[DownloadResult("land_ownership", "success")]))
    assert failed == 0 and "해당없음" in text and "모두 실패" not in text


def test_web_map_path_is_listed_only_when_created(tmp_path):
    assert "HTML 웹 지도" not in _text(_result(tmp_path))[0]
    text, _ = _text(_result(tmp_path, web_map_html=tmp_path / "project.html"))
    assert "HTML" in text and str(tmp_path / "project.html") in text


def test_summary_reports_only_outputs_that_exist(tmp_path):
    full, _ = _text(_with_shapefile(_result(tmp_path)))
    assert "✓ GeoPackage 생성" in full and "✓ SHP 생성: 1개" in full and "✓ QGIS 프로젝트 생성" in full
    assert "⚠" not in full

    bare, _ = _text(_result(tmp_path / "other", geopackage=None, project_file=None))
    assert "✓ GeoPackage" not in bare and "✓ SHP" not in bare and "✓ QGIS" not in bare
    assert "SHP가 생성되지 않았습니다" in bare


def test_summary_names_layers_that_fell_back_to_geopackage(tmp_path):
    issues = [
        ValidationIssue("warning", "SHAPEFILE_ENCODING_FALLBACK", "fallback", layer="land_ownership"),
        ValidationIssue("warning", "OTHER", "ignored", layer="power_line"),
    ]
    text, _ = _text(_with_shapefile(_result(tmp_path, issues=issues)))
    assert "1개 레이어는 SHP 대신 GeoPackage만 사용합니다: land_ownership" in text
    assert "power_line" not in text


def test_catalog_discovery_returns_definitions_error_or_none_when_cancelled():
    definitions = LayerCatalog().all()
    assert catalog_discovery_outcome(lambda: definitions, lambda: False, ENTERED_VWORLD) == {
        "definitions": definitions,
    }

    def failing():
        raise RuntimeError(f"catalog unavailable {ENTERED_VWORLD}")

    failure = catalog_discovery_outcome(failing, lambda: False, ENTERED_VWORLD)
    assert "catalog unavailable" in failure["error"] and ENTERED_VWORLD not in failure["error"]

    assert catalog_discovery_outcome(lambda: definitions, lambda: True, ENTERED_VWORLD) is None
    states = iter([False, True])
    assert catalog_discovery_outcome(lambda: definitions, lambda: next(states), ENTERED_VWORLD) is None
