"""QGIS-free decisions behind the RouteGIS wizard.

The wizard widgets only gather input and show text; every rule that picks a
value or words a message lives here so it can be tested with plain imports.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..errors import RouteGISError
from ..models import LayerDefinition, PipelineResult, ValidationIssue
from ..security import redact_sensitive_text

SOURCE_ENTERED = "입력란(저장값 포함)"
SOURCE_ENVIRONMENT = "환경변수 / 플러그인 .env"
SOURCE_MISSING = "미설정"

SHAPEFILE_FALLBACK_CODE = "SHAPEFILE_ENCODING_FALLBACK"


@dataclass(frozen=True)
class ResolvedCredential:
    value: str
    source: str

    @property
    def source_label(self) -> str:
        """Label for the log; "미설정" when no key exists."""
        return self.source if self.value else SOURCE_MISSING


def resolve_credential(entered: str, fallback: Callable[[], str]) -> ResolvedCredential:
    """The entered (trimmed) value wins; otherwise ask ``fallback`` (env, then .env)."""
    text = entered.strip()
    if text:
        return ResolvedCredential(text, SOURCE_ENTERED)
    return ResolvedCredential(fallback(), SOURCE_ENVIRONMENT)


def credential_log_line(label: str, credential: ResolvedCredential) -> str:
    return f"{label} 인증키: {credential.source_label}"


def order_selected_layers(checked_ids: Iterable[str], default_ids: Iterable[str]) -> tuple[str, ...]:
    """Default-profile layers first (profile order), then the rest in list order."""
    checked = tuple(checked_ids)
    checked_set = set(checked)
    defaults = tuple(default_ids)
    return tuple(item for item in defaults if item in checked_set) + tuple(
        item for item in checked if item not in defaults
    )


def parse_crs_code(text: str) -> str:
    """``"EPSG:5186 — 이름"`` -> ``"EPSG:5186"`` (upper-cased, trimmed)."""
    return text.split(" — ", 1)[0].strip().upper()


def coordinate_report_text(report: Any) -> str:
    min_x, min_y, max_x, max_y = report.bounds
    return (
        f"DXF 실제 좌표: X {min_x:,.3f} ~ {max_x:,.3f} / Y {min_y:,.3f} ~ {max_y:,.3f}\n"
        f"범위 중심: ({report.center[0]:,.3f}, {report.center[1]:,.3f}) · "
        f"$INSUNITS={report.insunits} ({report.unit_name}) · X=동쪽, Y=북쪽으로 해석"
    )


def cad_warning_suffix(warnings: Sequence[ValidationIssue]) -> str:
    if not warnings:
        return ""
    return "\n경고:\n" + "\n".join(f"- {issue.layer}: {issue.message}" for issue in warnings)


def recommendation_status_text(candidate_count: int, insunits: int) -> str:
    if candidate_count:
        text = (
            f"추천 후보 {candidate_count}개 · 시·도 대략 범위와 EPSG 사용범위 기준 (동점은 목록 순서). "
            "첫 후보를 미리보기에 임시 적용합니다. Bessel/GRS80 등은 지도에서 비교해 선택하세요."
        )
    else:
        text = (
            "해당 지역에 맞는 후보가 없습니다. 사업 지역, 지역 원점·임의 좌표, X/Y 뒤바뀜, "
            "경위도·mm 단위를 확인하거나 원본 좌표계를 직접 선택해 주세요."
        )
    if insunits not in (0, 6):
        text += " 도면 단위가 미터로 기록되어 있지 않습니다. 좌표 단위를 확인하세요 (자동 배율 변환 없음)."
    return text


def catalog_discovery_outcome(
    discover: Callable[[], list[LayerDefinition]],
    is_cancelled: Callable[[], bool],
    api_key: str,
) -> dict[str, Any] | None:
    """Shape a VWorld catalog discovery for the task callback.

    ``None`` when cancelled, ``{"error": redacted}`` on failure, otherwise
    ``{"definitions": [...]}``.
    """
    if is_cancelled():
        return None
    definitions = None
    error = None
    try:
        definitions = discover()
    except Exception as exc:
        error = redact_sensitive_text(exc, api_key)
    if is_cancelled():
        return None
    if error is not None:
        return {"error": error}
    return {"definitions": definitions}


def _shapefile_count(result: PipelineResult) -> int:
    root = result.project_directory / "shp"
    return sum(1 for _ in root.rglob("*.shp")) if root.is_dir() else 0


def output_lines(result: PipelineResult) -> list[str]:
    """Output summary built from what the pipeline actually produced."""
    lines = [
        f"✓ GeoPackage 생성: {result.geopackage}" if result.geopackage is not None
        else "⚠ GeoPackage가 생성되지 않았습니다."
    ]
    shapefiles = _shapefile_count(result)
    lines.append(f"✓ SHP 생성: {shapefiles}개" if shapefiles else "⚠ SHP가 생성되지 않았습니다.")
    fallback_layers = [
        issue.layer or "(이름 없음)" for issue in result.issues if issue.code == SHAPEFILE_FALLBACK_CODE
    ]
    if fallback_layers:
        lines.append(
            f"⚠ EUC-KR로 표현할 수 없는 속성값 때문에 {len(fallback_layers)}개 레이어는 SHP 대신 "
            f"GeoPackage만 사용합니다: {', '.join(fallback_layers)}"
        )
    lines.append(
        f"✓ QGIS 프로젝트 생성: {result.project_file}" if result.project_file is not None
        else "⚠ QGIS 프로젝트가 생성되지 않았습니다."
    )
    return lines


def build_result_lines(
    result: PipelineResult,
    describe_layer: Callable[[str], LayerDefinition],
    secrets: Sequence[str] = (),
) -> tuple[list[str], int]:
    """Return (summary lines, failed download count) from the real pipeline result."""
    lines = ["프로젝트 생성 결과"]
    failed_count = 0
    for item in result.downloads:
        definition = describe_layer(item.layer_id)
        label = f"{definition.name} [{definition.provider} / {item.layer_id}]"
        if item.status == "success":
            detail = f"성공 · {item.feature_count}개" if item.feature_count else "성공 · 0개 (해당없음)"
            lines.append(f"✓ {label}: {detail}")
        else:
            failed_count += 1
            cause = redact_sensitive_text(item.error, *secrets)
            lines.append(f"⚠ {label}: 실패\n    원인: {cause or '공급자가 상세 오류를 반환하지 않았습니다.'}")
    if failed_count:
        total = len(result.downloads)
        summary = (
            f"공간정보 {total}개 모두 실패했습니다."
            if failed_count == total
            else f"공간정보 {total}개 중 {failed_count}개가 실패했습니다."
        )
        lines.append(f"\n{summary} 위 원인을 확인해 주세요.")
    lines.extend(output_lines(result))
    lines.extend([
        f"\n{result.state.value}",
        f"보고서: {result.report_html}",
        f"로그: {result.project_directory / 'logs' / 'app.log'}",
    ])
    if result.web_map_html is not None:
        lines.append(
            f"✓ HTML 웹 지도: {result.web_map_html}\n"
            "  이 HTML 파일 하나만 복사해 브라우저로 열 수 있습니다. 배경지도 선택 시에는 인터넷이 필요합니다."
        )
    return lines, failed_count


def stored_credentials(vworld_api_key: str, data_go_kr_service_key: str) -> dict[str, str]:
    """Trimmed values to persist, keyed by setting name; blank means "remove"."""
    return {
        "vworld_api_key": vworld_api_key.strip(),
        "data_go_kr_service_key": data_go_kr_service_key.strip(),
    }


def validated_output_directory(text: str) -> Path:
    stripped = text.strip()
    if not stripped:
        raise RouteGISError("프로젝트 저장 위치를 선택해 주세요.")
    return Path(stripped)
