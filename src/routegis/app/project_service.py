from __future__ import annotations

import json
import hashlib
import logging
import shutil
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

from shapely.geometry import LineString

from ..cad.reader import DxfCoordinateReport, DxfReader
from ..config import AppSettings, load_settings, safe_project_name, validate_project_config
from ..errors import (
    CadReadError,
    CrsError,
    ExportError,
    ProviderError,
    RouteGISError,
)
from ..gis.crs import (
    korea_sanity_issues,
    normalize_crs,
    transform_layer,
    validate_projected_metre_crs,
)
from ..gis.export import export_layers
from ..gis.geometry import create_aoi_layer, create_linework_layer
from ..gis.validator import validate_layer, validate_layer_intersects_aoi, validate_route_and_aoi
from ..models import (
    DownloadResult,
    LayerDefinition,
    PipelineResult,
    PipelineState,
    ProjectConfig,
    RouteGeometry,
    SpatialLayer,
    ValidationIssue,
)
from ..providers import EcologyClient, LayerCatalog, OverpassClient, SpatialDataProvider, VWorldClient
from ..qgis.project import write_qgis_project
from ..report import GEOMETRY_POLICY, write_reports
from ..web_map import write_web_map
from .downloads import fetch_provider_groups
from .project_log import _close_project_logger, _logger, _redact
from .publish import _publish_project, _staging_directory, _validate_existing_project


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_metadata(definition: LayerDefinition) -> dict[str, str]:
    attribution = (
        "© OpenStreetMap contributors"
        if definition.provider == "osm"
        else definition.publisher
    )
    return {
        "id": definition.id,
        "name": definition.name,
        "provider": definition.provider,
        "data_id": definition.data_id,
        "dataset_id": definition.dataset_id,
        "publisher": definition.publisher,
        "attribution": attribution,
        "license_name": definition.license_name,
        "license_url": definition.license_url,
    }


def _download_payload(result: DownloadResult, definition: LayerDefinition) -> dict[str, Any]:
    return {
        "id": definition.id,
        "name": definition.name,
        "status": result.status,
        "feature_count": result.feature_count,
        "error": result.error,
        "provider": definition.provider,
        "data_id": definition.data_id,
        "dataset_id": definition.dataset_id,
        "publisher": definition.publisher,
        "license_name": definition.license_name,
        "license_url": definition.license_url,
        "initial_tile_count": result.initial_tile_count,
        "request_count": result.request_count,
        "successful_tile_count": result.successful_tile_count,
        "failed_tile_count": result.failed_tile_count,
        "failed_tile_bbox": result.failed_tile_bbox,
    }


def _cad_skip_issues(reader: DxfReader) -> list[ValidationIssue]:
    by_layer = defaultdict(list)
    for entity in reader.skipped_entities:
        by_layer[entity.layer].append(entity)
    issues: list[ValidationIssue] = []
    for layer, entities in sorted(by_layer.items(), key=lambda item: item[0].casefold()):
        type_counts = Counter(entity.entity_type for entity in entities)
        type_summary = ", ".join(f"{name} {count}개" for name, count in sorted(type_counts.items()))
        handles = ", ".join(entity.handle for entity in entities)
        issues.append(
            ValidationIssue(
                severity="warning",
                code="SKIPPED_CAD_ENTITY",
                message=(
                    f"길이 0 CAD 객체 {len(entities)}개를 제외했습니다. "
                    f"유형: {type_summary}. Handles: {handles}"
                ),
                layer=layer,
            )
        )
    return issues


def _geometry_family(geometry_type: str) -> str | None:
    return next(
        (
            family
            for family in ("Polygon", "LineString", "Point")
            if family in geometry_type
        ),
        None,
    )


def _provider_contract_issues(
    definition: LayerDefinition,
    layer: SpatialLayer,
    aoi: SpatialLayer,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    try:
        crs_matches = normalize_crs(layer.crs) == normalize_crs(aoi.crs)
    except CrsError:
        crs_matches = False
    if not crs_matches:
        issues.append(
            ValidationIssue(
                severity="error",
                code="PROVIDER_CRS_MISMATCH",
                message=(
                    f"공급자 레이어 좌표계({layer.crs})가 프로젝트 좌표계"
                    f"({aoi.crs})와 다릅니다."
                ),
                layer=definition.id,
            )
        )

    expected_family = _geometry_family(definition.geometry_type)
    actual_family = _geometry_family(layer.geometry_type)
    if expected_family is not None and actual_family != expected_family:
        issues.append(
            ValidationIssue(
                severity="error",
                code="PROVIDER_GEOMETRY_TYPE_MISMATCH",
                message=(
                    f"공급자 레이어 Geometry 유형({layer.geometry_type})이 카탈로그 정의"
                    f"({definition.geometry_type})와 다릅니다."
                ),
                layer=definition.id,
            )
        )
    return issues


Report = Callable[[int, str], None]


def _copy_source_drawing(config: ProjectConfig, project_directory: Path) -> tuple[Path, str]:
    source_copy = project_directory / "source" / config.source_drawing.name
    shutil.copy2(config.source_drawing, source_copy)
    return source_copy, _sha256(source_copy)


def _build_route(
    config: ProjectConfig,
    lines_by_layer: dict[str, list[LineString]],
) -> RouteGeometry:
    source_layers = tuple(sorted(lines_by_layer, key=str.casefold))
    original_layer = create_linework_layer(lines_by_layer, config.source_crs)
    original_layer.name = "linework_original"
    original_layer.display_name = "원본 선형"
    original_layer.category = "source"
    transformed_layer = transform_layer(original_layer, config.output_crs)
    for feature in transformed_layer.features:
        feature.properties["length_m"] = feature.geometry.length
    transformed_layer.name = "linework"
    transformed_layer.display_name = "선형"
    transformed_layer.category = "route"
    return RouteGeometry(
        original=original_layer,
        transformed=transformed_layer,
        source_layers=source_layers,
        source_crs=config.source_crs,
        output_crs=config.output_crs,
    )


def _judge_download(
    definition: LayerDefinition,
    outcome: DownloadResult | Exception,
    aoi: SpatialLayer,
    secrets: tuple[str, ...],
    log: logging.Logger,
) -> tuple[DownloadResult, list[ValidationIssue] | None]:
    """Accept or reject one provider outcome; returns the result and its layer issues."""
    layer_issues: list[ValidationIssue] | None = None
    try:
        if isinstance(outcome, Exception):
            raise outcome
        result = outcome
        if result.layer_id != definition.id:
            raise RouteGISError(
                "공급자 결과 레이어 ID가 요청과 다릅니다: "
                f"{result.layer_id} (예상 {definition.id})"
            )
        result.error = _redact(result.error, *secrets)
        if result.status != "success":
            result.status = "failed"
            result.layer = None
            result.feature_count = 0
            if not result.error:
                result.error = "공급자가 레이어를 완전하게 반환하지 않았습니다."
            log.warning("레이어 다운로드 실패: %s: %s", definition.id, result.error)
        elif result.layer is None:
            result.status = "failed"
            result.feature_count = 0
            result.error = "공급자가 완전한 레이어를 반환하지 않았습니다."
            log.warning("레이어 다운로드 실패: %s: %s", definition.id, result.error)
        else:
            result.feature_count = len(result.layer.features)
            layer_issues = _provider_contract_issues(definition, result.layer, aoi)
            if not any(issue.severity == "error" for issue in layer_issues):
                layer_issues += validate_layer(result.layer) + validate_layer_intersects_aoi(result.layer, aoi)
            validation_errors = [
                issue for issue in layer_issues if issue.severity == "error"
            ]
            if validation_errors:
                result.status = "failed"
                details = "; ".join(
                    f"{issue.code}: {issue.message}"
                    for issue in validation_errors[:3]
                )
                omitted = len(validation_errors) - 3
                suffix = f" (그 외 {omitted}건)" if omitted > 0 else ""
                result.error = f"Geometry 또는 좌표계 검증에 실패했습니다: {details}{suffix}"
                result.layer = None
                result.feature_count = 0
    except Exception as exc:
        log.warning("레이어 다운로드 실패: %s: %s", definition.id, exc)
        result = DownloadResult(layer_id=definition.id, status="failed", error=_redact(str(exc), *secrets))
    return result, layer_issues


def _export_layers_and_metadata(
    definitions: list[LayerDefinition],
    downloads: list[DownloadResult],
    route_layer: SpatialLayer,
    aoi: SpatialLayer,
) -> tuple[list[SpatialLayer], Any]:
    definitions_by_id = {definition.id: definition for definition in definitions}
    successful_results = [
        result
        for result in downloads
        if result.status == "success" and result.layer is not None
    ]
    successful_layers: list[SpatialLayer] = []
    for result in successful_results:
        definition = definitions_by_id.get(result.layer_id)
        layer = result.layer
        if definition is None or layer is None:
            raise ExportError(f"공급자 레이어 ID가 선택 목록과 일치하지 않습니다: {result.layer_id}")
        if layer.name != definition.id or layer.category != definition.category:
            raise ExportError(
                "공급자 레이어 메타데이터가 선택 정의와 일치하지 않습니다: "
                f"{result.layer_id}"
            )
        successful_layers.append(
            replace(
                layer,
                name=definition.id,
                display_name=definition.name,
                category=definition.category,
            )
        )
    layer_metadata = MappingProxyType(
        {
            result.layer_id: MappingProxyType(
                _artifact_metadata(definitions_by_id[result.layer_id])
            )
            for result in successful_results
        }
    )
    return [route_layer, aoi, *successful_layers], layer_metadata


def _shapefile_fallback_issues(
    layers: list[SpatialLayer], shapefile_mappings: Any
) -> list[ValidationIssue]:
    return [
        ValidationIssue(
            severity="warning",
            code="SHAPEFILE_ENCODING_FALLBACK",
            message=(
                "EUC-KR로 표현할 수 없는 속성값이 있어 Shapefile 대신 "
                "GeoPackage를 사용합니다."
            ),
            layer=layer.name,
        )
        for layer in layers
        if layer.name not in shapefile_mappings
    ]


def _manifest_payload(
    config: ProjectConfig,
    *,
    state: PipelineState,
    source_digest: str,
    route: RouteGeometry,
    aoi: SpatialLayer,
    definitions: list[LayerDefinition],
    downloads: list[DownloadResult],
    project_file: Path,
    web_map_html: Path,
) -> dict[str, Any]:
    definitions_by_id = {definition.id: definition for definition in definitions}
    return {
        "project": config.project_name,
        "status": state.value,
        "source": {
            "filename": config.source_drawing.name,
            "sha256": source_digest,
            "layers": list(route.source_layers),
        },
        "route": {
            "source_crs": config.source_crs,
            "output_crs": config.output_crs,
            "buffer": config.buffer_distance,
            "length_m": route.length,
            "bbox": route.bbox,
            "aoi_bbox": aoi.bounds,
            "chord_tolerance": config.chord_tolerance,
        },
        "layers": [
            _download_payload(result, definitions_by_id[result.layer_id])
            for result in downloads
        ],
        "outputs": {
            "geopackage": "data/project.gpkg",
            "project": project_file.name,
            "report": "reports/processing_report.html",
            "web_map": web_map_html.name,
        },
    }


def _report_payload(
    config: ProjectConfig,
    *,
    state: PipelineState,
    route: RouteGeometry,
    aoi: SpatialLayer,
    manifest_payload: dict[str, Any],
    issues: list[ValidationIssue],
    project_file: Path,
    web_map_html: Path,
) -> dict[str, Any]:
    return {
        "project": config.project_name,
        "status": state.value,
        "source": config.source_drawing.name,
        "route_length_m": route.length,
        "crs": config.output_crs,
        "buffer_m": config.buffer_distance,
        "aoi_area_m2": aoi.features[0].geometry.area,
        "geometry_policy": GEOMETRY_POLICY,
        "layers": manifest_payload["layers"],
        "issues": [asdict(issue) for issue in issues],
        "outputs": ["project.gpkg", project_file.name, web_map_html.name, "project_manifest.json"],
    }


class ProjectService:
    def __init__(self, settings: AppSettings | None = None):
        self.settings = settings or load_settings()
        self.catalog: LayerCatalog = LayerCatalog()
        self.last_cad_warnings: tuple[ValidationIssue, ...] = ()
        self.last_coordinate_report: DxfCoordinateReport | None = None
        self.last_cad_lines: dict[str, list[LineString]] = {}

    def analyze_drawing(self, drawing: Path, *, chord_tolerance: float | None = None,
                        on_progress: Callable[[int, str], None] | None = None) -> list[dict[str, Any]]:
        self.last_cad_warnings = ()
        self.last_coordinate_report = None
        self.last_cad_lines = {}
        notify = on_progress or (lambda percent, message: None)
        notify(0, "도면 분석 준비 중")
        reader = DxfReader(
            drawing,
            self.settings.chord_tolerance
            if chord_tolerance is None
            else chord_tolerance,
            on_progress=notify,
        )
        inventory = reader.inventory()
        notify(85, "CAD 레이어별 객체 수와 길이 집계 완료 · 실제 좌표 범위 확인 중")
        warnings = tuple(_cad_skip_issues(reader))
        if not inventory and reader.skipped_entities:
            raise CadReadError("도면의 지원 선 객체가 모두 길이 0이라 분석할 수 없습니다.")
        report = reader.coordinate_report()
        lines = reader.lines_by_layer()
        candidates = [asdict(item) for item in inventory]
        notify(100, f"도면 분석 완료 · CAD 레이어 {len(candidates)}개")
        self.last_cad_warnings = warnings
        self.last_coordinate_report = report
        self.last_cad_lines = lines
        return candidates

    def _provider(
        self,
        provider_name: str,
        *,
        api_key: str,
        data_go_kr_service_key: str,
        on_progress: Callable[[str], None] | None = None,
        check_cancelled: Callable[[], None] | None = None,
    ) -> SpatialDataProvider:
        common = {
            "request_budget_minimum": self.settings.download_request_budget_minimum,
            "request_budget_per_tile": self.settings.download_request_budget_per_tile,
            "request_budget_maximum": self.settings.download_request_budget_maximum,
            "max_split_depth": self.settings.download_max_split_depth,
            "minimum_tile_size_m": self.settings.download_minimum_tile_size_m,
            "max_response_bytes": self.settings.download_max_response_bytes,
            "on_progress": on_progress,
            "check_cancelled": check_cancelled,
        }
        if provider_name == "vworld":
            return VWorldClient(
                api_key,
                timeout=self.settings.vworld_timeout,
                retries=self.settings.vworld_retry,
                backoff=self.settings.vworld_backoff,
                page_size=self.settings.vworld_page_size,
                land_ownership_tile_size_m=self.settings.vworld_land_ownership_tile_size_m,
                default_tile_size_m=self.settings.vworld_default_tile_size_m,
                **common,
            )
        if provider_name == "ecology":
            return EcologyClient(
                data_go_kr_service_key,
                timeout=self.settings.ecology_timeout,
                retries=self.settings.ecology_retry,
                page_size=self.settings.ecology_page_size,
                tile_size_m=self.settings.ecology_tile_size_m,
                **common,
            )
        if provider_name == "osm":
            return OverpassClient(
                endpoint=self.settings.osm_endpoint,
                timeout=self.settings.osm_timeout,
                retries=self.settings.osm_retry,
                tile_size_m=self.settings.osm_tile_size_m,
                **common,
            )
        raise ProviderError(f"지원하지 않는 공간정보 공급자입니다: {provider_name}")

    def build(
        self,
        config: ProjectConfig,
        *,
        api_key: str = "",
        data_go_kr_service_key: str = "",
        provider: SpatialDataProvider | None = None,
        on_progress: Callable[[int, str], None] | None = None,
        check_cancelled: Callable[[], None] | None = None,
    ) -> PipelineResult:
        def notify(percent: int, message: str) -> None:
            if check_cancelled is not None:
                check_cancelled()
            if on_progress is not None:
                on_progress(percent, _redact(message, api_key, data_go_kr_service_key) or "")

        notify(0, "입력 설정과 출력 위치 확인 중")
        validate_project_config(config, self.catalog.snapshot())
        validate_projected_metre_crs(config.source_crs)
        validate_projected_metre_crs(config.output_crs)
        project_directory = config.output_directory / safe_project_name(config.project_name)
        _validate_existing_project(project_directory, config.project_name)
        staging = _staging_directory(project_directory)
        try:
            result = self._build_in_directory(
                config,
                project_directory=staging,
                api_key=api_key,
                data_go_kr_service_key=data_go_kr_service_key,
                provider=provider,
                on_progress=notify,
                check_cancelled=check_cancelled,
            )
            notify(99, "완성된 프로젝트를 최종 폴더에 저장 중")
            _close_project_logger(staging)
        except BaseException:
            _close_project_logger(staging)
            shutil.rmtree(staging, ignore_errors=True)
            raise

        try:
            _publish_project(staging, project_directory)
        except Exception as exc:
            # The build is complete: retain its artifacts and diagnostics so a
            # filesystem failure does not force another download/conversion.
            try:
                log = _logger(staging / "logs" / "app.log", api_key, data_go_kr_service_key)
                log.error("최종 저장 실패 · 완성된 임시 결과 보존: %s", exc)
            except OSError:
                pass  # Do not mask the original failure if logging is also blocked.
            finally:
                _close_project_logger(staging)
            raise

        result.project_directory = project_directory
        for attribute in ("project_file", "geopackage", "report_html", "manifest", "web_map_html"):
            path = getattr(result, attribute)
            if path is not None:
                setattr(result, attribute, project_directory / path.relative_to(staging))
        return result

    def _read_linework(
        self,
        config: ProjectConfig,
        source_copy: Path,
        report: Report,
        log: logging.Logger,
    ) -> tuple[dict[str, list[LineString]], list[ValidationIssue]]:
        reader = DxfReader(source_copy, config.chord_tolerance,
                           on_progress=lambda percent, message: report(8 + 7 * percent // 100, message))
        lines_by_layer = reader.lines_by_layer()
        cad_warnings = _cad_skip_issues(reader)
        self.last_cad_warnings = tuple(cad_warnings)
        for issue in cad_warnings:
            log.warning("CAD 객체 제외: %s: %s", issue.layer, issue.message)
        if not lines_by_layer:
            if reader.skipped_entities:
                raise CadReadError("도면의 지원 선 객체가 모두 길이 0이라 프로젝트를 생성할 수 없습니다.")
            raise RouteGISError("도면에 지원하는 선형 객체가 없습니다.")
        return lines_by_layer, cad_warnings

    def _download_layers(
        self,
        definitions: list[LayerDefinition],
        aoi: SpatialLayer,
        issues: list[ValidationIssue],
        *,
        api_key: str,
        data_go_kr_service_key: str,
        provider: SpatialDataProvider | None,
        report: Report,
        log: logging.Logger,
        check_cancelled: Callable[[], None] | None,
    ) -> list[DownloadResult]:
        secrets = (api_key, data_go_kr_service_key)
        completed: dict[int, DownloadResult] = {}
        download_issues: dict[int, list[ValidationIssue]] = {}
        labels = [f"[{index + 1}/{len(definitions)}] {definition.name} [{definition.provider}]"
                  for index, definition in enumerate(definitions)]

        def download_progress(index: int, message: str) -> None:
            report(30 + 45 * len(completed) // len(definitions), f"{labels[index]} · {message}")

        def accept_download(index: int, outcome: DownloadResult | Exception) -> None:
            result, layer_issues = _judge_download(definitions[index], outcome, aoi, secrets, log)
            if layer_issues is not None:
                download_issues[index] = layer_issues
            completed[index] = result
            detail = (f"성공 · {result.feature_count}개" if result.status == "success"
                      else f"실패 · {result.error or '상세 오류 없음'}")
            report(30 + 45 * len(completed) // len(definitions),
                   f"{labels[index]} {detail} · API 요청 {result.request_count}회 · 완료 타일 {result.successful_tile_count}개")

        fetch_provider_groups(
            definitions, aoi, provider=provider,
            create_provider=lambda name, progress, check: self._provider(
                name, api_key=api_key, data_go_kr_service_key=data_go_kr_service_key,
                on_progress=progress, check_cancelled=check,
            ),
            on_progress=download_progress, on_result=accept_download,
            on_close_error=lambda exc: log.warning("공급자 종료 실패: %s", exc),
            check_cancelled=check_cancelled,
        )
        downloads = [completed[index] for index in range(len(definitions))]
        for index in range(len(definitions)):
            issues.extend(download_issues.get(index, []))
        return downloads

    def _build_in_directory(
        self,
        config: ProjectConfig,
        *,
        project_directory: Path,
        api_key: str,
        data_go_kr_service_key: str,
        provider: SpatialDataProvider | None,
        on_progress: Callable[[int, str], None],
        check_cancelled: Callable[[], None] | None,
    ) -> PipelineResult:
        secrets = (api_key, data_go_kr_service_key)
        log = _logger(project_directory / "logs" / "app.log", *secrets)
        log.info("프로젝트 생성을 시작합니다: %s", config.project_name)

        def report(percent: int, message: str) -> None:
            log.info(message)
            on_progress(percent, message)

        report(3, "원본 도면 복사 및 확인 중")
        source_copy, source_digest = _copy_source_drawing(config, project_directory)

        report(8, "DXF 도면의 선 객체 읽는 중")
        lines_by_layer, cad_warnings = self._read_linework(config, source_copy, report, log)
        report(16, "선형 레이어 생성 및 좌표계 변환 중")
        route = _build_route(config, lines_by_layer)
        transformed_layer = route.transformed
        report(22, "분석범위 Buffer 생성 중")
        aoi = create_aoi_layer(transformed_layer, config.buffer_distance)
        report(27, "선형·분석범위 Geometry 및 좌표계 검증 중")
        issues = cad_warnings + validate_route_and_aoi(transformed_layer, aoi) + korea_sanity_issues(transformed_layer)
        if any(issue.severity == "error" for issue in issues):
            raise RouteGISError("노선 또는 분석 범위 자동 검증에 실패했습니다.")

        definitions = self.catalog.select(config.selected_layers)
        downloads: list[DownloadResult] = []
        report(30, f"공간정보 {len(definitions)}개 수집 시작")
        if definitions:
            downloads = self._download_layers(
                definitions, aoi, issues,
                api_key=api_key, data_go_kr_service_key=data_go_kr_service_key,
                provider=provider, report=report, log=log, check_cancelled=check_cancelled,
            )

        layers, layer_metadata = _export_layers_and_metadata(definitions, downloads, transformed_layer, aoi)
        report(78, "GeoPackage 및 Shapefile 저장 중")
        geopackage, shapefile_mappings = export_layers(
            project_directory,
            layers,
            export_shapefiles=config.export_shapefile,
            layer_metadata=layer_metadata,
        )
        if config.export_shapefile:
            issues.extend(_shapefile_fallback_issues(layers, shapefile_mappings))
        report(88, "QGIS 프로젝트 구성 및 스타일 저장 중")
        project_file = write_qgis_project(
            project_directory / f"{safe_project_name(config.project_name)}.qgz",
            config.project_name,
            geopackage,
            layers,
            source_copy,
            config.source_crs,
            template_version=self.settings.qgis_template_version,
            layer_metadata=layer_metadata,
        )
        report(90, "단일 HTML 웹 지도 생성 중 · 원본 CAD 제외")
        web_map_html = write_web_map(
            project_file.with_suffix(".html"), config.project_name, layers,
            layer_metadata={definition.id: _artifact_metadata(definition) for definition in definitions},
            downloads=downloads, secrets=secrets,
            on_progress=lambda percent, message: report(90 + 4 * percent // 100, message),
        )
        state = (
            PipelineState.COMPLETED_WITH_WARNINGS
            if any(issue.severity == "warning" for issue in issues)
            or any(result.status != "success" for result in downloads)
            else PipelineState.COMPLETED
        )
        report(95, "Manifest 및 처리 보고서 작성 중")
        manifest_payload = _manifest_payload(
            config, state=state, source_digest=source_digest, route=route, aoi=aoi,
            definitions=definitions, downloads=downloads,
            project_file=project_file, web_map_html=web_map_html,
        )
        manifest = project_directory / "project_manifest.json"
        manifest.write_text(json.dumps(manifest_payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        report_payload = _report_payload(
            config, state=state, route=route, aoi=aoi, manifest_payload=manifest_payload,
            issues=issues, project_file=project_file, web_map_html=web_map_html,
        )
        report_html, _ = write_reports(project_directory / "reports", report_payload)
        log.info("프로젝트 생성 상태: %s", state.value)
        _close_project_logger(project_directory)
        return PipelineResult(
            state=state,
            project_directory=project_directory,
            project_file=project_file,
            geopackage=geopackage,
            report_html=report_html,
            manifest=manifest,
            downloads=downloads,
            issues=issues,
            web_map_html=web_map_html,
        )
