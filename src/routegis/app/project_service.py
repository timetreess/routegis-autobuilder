from __future__ import annotations

import json
import hashlib
import logging
import shutil
import tempfile
import time
import uuid
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

from shapely.geometry import LineString

from ..cad.converter import DwgConverter
from ..cad.reader import DxfCoordinateReport, DxfReader
from ..config import AppSettings, load_settings, safe_project_name, validate_project_config
from ..errors import (
    CadReadError,
    ConfigurationError,
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
from ..gis.shapefile import write_shapefile
from ..gis.validator import validate_layer, validate_layer_intersects_aoi, validate_route_and_aoi
from ..models import (
    DownloadResult,
    LayerDefinition,
    PipelineResult,
    PipelineState,
    ProjectConfig,
    RouteGeometry,
    SpatialFeature,
    SpatialLayer,
    ValidationIssue,
)
from ..providers import EcologyClient, LayerCatalog, OverpassClient, SpatialDataProvider, VWorldClient
from ..qgis.project import write_qgis_project
from ..report import GEOMETRY_POLICY, write_reports
from ..security import redact_sensitive_text


def _redact(message: str | None, *secrets: str) -> str | None:
    return redact_sensitive_text(message, *secrets)


class _SecretFilter(logging.Filter):
    def __init__(self, *secrets: str):
        super().__init__()
        self.secrets = tuple(secret for secret in secrets if secret)

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redact(str(record.msg), *self.secrets) or ""

        def sanitized(value: object) -> object:
            rendered = str(value)
            redacted = _redact(rendered, *self.secrets) or ""
            return redacted if redacted != rendered else value

        if isinstance(record.args, tuple):
            record.args = tuple(sanitized(arg) for arg in record.args)
        elif isinstance(record.args, dict):
            record.args = {key: sanitized(value) for key, value in record.args.items()}
        return True


def _logger(path: Path, *secrets: str) -> logging.Logger:
    logger = logging.getLogger(f"routegis.{path.parents[1].name}")
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    handler.addFilter(_SecretFilter(*secrets))
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def _close_project_logger(project_directory: Path) -> None:
    logger = logging.getLogger(f"routegis.{project_directory.name}")
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)


PROJECT_DIRECTORIES = (
    "source",
    "data/raw",
    "shp/route",
    "shp/cadastral",
    "shp/land",
    "shp/ecology",
    "shp/heritage",
    "shp/water",
    "shp/power",
    "shp/landuse",
    "shp/administrative",
    "shp/facility",
    "shp/source",
    "style",
    "reports",
    "logs",
)


def _directories(project_directory: Path) -> None:
    for relative in PROJECT_DIRECTORIES:
        (project_directory / relative).mkdir(parents=True, exist_ok=True)


def _validate_existing_project(project_directory: Path, project_name: str) -> None:
    if not project_directory.exists():
        return
    if not project_directory.is_dir():
        raise ConfigurationError(f"프로젝트 출력 경로가 폴더가 아닙니다: {project_directory}")
    if not any(project_directory.iterdir()):
        return
    manifest = project_directory / "project_manifest.json"
    if not manifest.is_file():
        raise ConfigurationError(
            f"기존 폴더를 덮어쓸 수 없습니다. RouteGIS 프로젝트가 아닙니다: {project_directory}"
        )
    try:
        existing = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"기존 RouteGIS Manifest를 읽을 수 없습니다: {manifest}") from exc
    if existing.get("project") != project_name:
        raise ConfigurationError(
            f"같은 출력 폴더에 다른 RouteGIS 프로젝트가 있습니다: {project_directory}"
        )


def _staging_directory(project_directory: Path) -> Path:
    project_directory.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{project_directory.name}.build-",
            dir=project_directory.parent,
        )
    )
    try:
        _directories(staging)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return staging


def _publish_project(staging: Path, project_directory: Path) -> None:
    backup: Path | None = None
    try:
        _close_project_logger(project_directory)
        if project_directory.exists():
            backup = project_directory.with_name(f".{project_directory.name}.backup-{uuid.uuid4().hex}")
            _replace_directory(project_directory, backup)
        _replace_directory(staging, project_directory)
    except BaseException as exc:
        try:
            if project_directory.exists() and not staging.exists():
                _replace_directory(project_directory, staging)
            if backup is not None and backup.exists() and not project_directory.exists():
                _replace_directory(backup, project_directory)
        except OSError as rollback_error:
            raise ConfigurationError("프로젝트 교체 실패 후 기존 결과를 복구할 수 없습니다.") from rollback_error
        if isinstance(exc, OSError):
            raise ConfigurationError(
                "기존 결과 파일이 사용 중입니다. QGIS에서 해당 프로젝트와 레이어를 닫은 뒤 다시 실행해 주세요."
            ) from exc
        raise
    if backup is not None:
        shutil.rmtree(backup, ignore_errors=True)


def _replace_directory(source: Path, target: Path) -> None:
    for attempt in range(5):
        try:
            source.replace(target)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.1 * (2**attempt))


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


class ProjectService:
    def __init__(self, settings: AppSettings | None = None):
        self.settings = settings or load_settings()
        self.catalog: LayerCatalog = LayerCatalog()
        self.last_cad_warnings: tuple[ValidationIssue, ...] = ()
        self.last_coordinate_report: DxfCoordinateReport | None = None
        self.last_cad_lines: dict[str, list[LineString]] = {}

    def analyze_drawing(self, drawing: Path, *, chord_tolerance: float | None = None) -> list[dict[str, Any]]:
        self.last_cad_warnings = ()
        self.last_coordinate_report = None
        self.last_cad_lines = {}
        reader = DxfReader(
            drawing,
            self.settings.chord_tolerance
            if chord_tolerance is None
            else chord_tolerance,
        )
        from ..cad.route_detector import rank_candidates

        inventory = reader.inventory(self.settings.snap_tolerance)
        self.last_cad_warnings = tuple(_cad_skip_issues(reader))
        if not inventory and reader.skipped_entities:
            raise CadReadError("도면의 지원 선 객체가 모두 길이 0이라 분석할 수 없습니다.")
        self.last_coordinate_report = reader.coordinate_report()
        self.last_cad_lines = reader.lines_by_layer()
        return [asdict(item) for item in rank_candidates(inventory)]

    def create_preview_shapefiles(
        self,
        drawing: Path,
        *,
        source_crs: str,
        output_directory: Path,
    ) -> dict[str, Path]:
        validate_projected_metre_crs(source_crs)
        reader = DxfReader(drawing, self.settings.chord_tolerance)
        lines_by_layer = reader.lines_by_layer()
        self.last_cad_warnings = tuple(_cad_skip_issues(reader))
        if not lines_by_layer:
            if reader.skipped_entities:
                raise CadReadError("도면의 지원 선 객체가 모두 길이 0이라 미리보기를 만들 수 없습니다.")
            raise RouteGISError("미리보기로 만들 수 있는 선 객체가 없습니다.")
        output_directory.mkdir(parents=True, exist_ok=True)
        paths: dict[str, Path] = {}
        for index, layer_name in enumerate(sorted(lines_by_layer, key=str.casefold), start=1):
            preview_layer = SpatialLayer(
                name=f"preview_{index:03d}",
                display_name=layer_name,
                crs=source_crs,
                geometry_type="LineString",
                category="source",
                features=[
                    SpatialFeature(
                        line,
                        {
                            "source_layer": layer_name,
                            "entity_index": entity_index,
                            "length_m": line.length,
                        },
                        f"{index}-{entity_index}",
                    )
                    for entity_index, line in enumerate(lines_by_layer[layer_name], start=1)
                ],
                source="cad_preview",
            )
            filename = f"{index:03d}_{safe_project_name(layer_name)}.shp"
            path = output_directory / filename
            write_shapefile(path, preview_layer)
            paths[layer_name] = path
        return paths

    def _provider(
        self,
        provider_name: str,
        *,
        api_key: str,
        data_go_kr_service_key: str,
        domain: str | None,
        on_progress: Callable[[str], None] | None = None,
    ) -> SpatialDataProvider:
        common = {
            "request_budget_minimum": self.settings.download_request_budget_minimum,
            "request_budget_per_tile": self.settings.download_request_budget_per_tile,
            "request_budget_maximum": self.settings.download_request_budget_maximum,
            "max_split_depth": self.settings.download_max_split_depth,
            "minimum_tile_size_m": self.settings.download_minimum_tile_size_m,
            "max_response_bytes": self.settings.download_max_response_bytes,
            "on_progress": on_progress,
        }
        if provider_name == "vworld":
            return VWorldClient(
                api_key,
                domain=domain,
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
        domain: str | None = None,
        provider: SpatialDataProvider | None = None,
        on_progress: Callable[[int, str], None] | None = None,
        use_native_project: bool = True,
    ) -> PipelineResult:
        def notify(percent: int, message: str) -> None:
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
                domain=domain,
                provider=provider,
                on_progress=notify,
                use_native_project=use_native_project,
            )
            notify(99, "완성된 프로젝트를 최종 폴더에 저장 중")
            _close_project_logger(staging)
            _publish_project(staging, project_directory)
        except BaseException:
            _close_project_logger(staging)
            shutil.rmtree(staging, ignore_errors=True)
            raise

        result.project_directory = project_directory
        for attribute in ("project_file", "geopackage", "report_html", "manifest"):
            path = getattr(result, attribute)
            if path is not None:
                setattr(result, attribute, project_directory / path.relative_to(staging))
        return result

    def _build_in_directory(
        self,
        config: ProjectConfig,
        *,
        project_directory: Path,
        api_key: str,
        data_go_kr_service_key: str,
        domain: str | None,
        provider: SpatialDataProvider | None,
        on_progress: Callable[[int, str], None],
        use_native_project: bool,
    ) -> PipelineResult:
        secrets = (api_key, data_go_kr_service_key)
        log = _logger(project_directory / "logs" / "app.log", *secrets)
        log.info("프로젝트 생성을 시작합니다: %s", config.project_name)

        def report(percent: int, message: str) -> None:
            log.info(message)
            on_progress(percent, message)

        report(3, "원본 도면 복사 및 확인 중")
        source_copy = project_directory / "source" / config.source_drawing.name
        shutil.copy2(config.source_drawing, source_copy)
        source_digest = _sha256(source_copy)
        dxf_path = source_copy
        if source_copy.suffix.lower() == ".dwg":
            if config.dwg_converter is None:
                raise RouteGISError("DWG 변환기가 지정되지 않았습니다.")
            dxf_path = DwgConverter(config.dwg_converter).convert(
                source_copy,
                project_directory / "data" / "raw" / f"{source_copy.stem}.dxf",
            )

        report(8, "DXF 도면의 선 객체 읽는 중")
        reader = DxfReader(dxf_path, config.chord_tolerance)
        lines_by_layer = reader.lines_by_layer()
        cad_warnings = _cad_skip_issues(reader)
        self.last_cad_warnings = tuple(cad_warnings)
        for issue in cad_warnings:
            log.warning("CAD 객체 제외: %s: %s", issue.layer, issue.message)
        if not lines_by_layer:
            if reader.skipped_entities:
                raise CadReadError("도면의 지원 선 객체가 모두 길이 0이라 프로젝트를 생성할 수 없습니다.")
            raise RouteGISError("도면에 지원하는 선형 객체가 없습니다.")
        source_layers = tuple(sorted(lines_by_layer, key=str.casefold))
        report(16, "선형 레이어 생성 및 좌표계 변환 중")
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
        if config.project_type == "linear":
            buffer_source_layers = config.source_layers
            buffer_lines = {layer_name: reader.read_layer(layer_name) for layer_name in buffer_source_layers}
            buffer_source = transform_layer(create_linework_layer(buffer_lines, config.source_crs), config.output_crs)
        else:
            buffer_source_layers = source_layers
            buffer_source = transformed_layer
        route = RouteGeometry(
            original=original_layer,
            transformed=transformed_layer,
            source_layers=source_layers,
            source_crs=config.source_crs,
            output_crs=config.output_crs,
        )
        report(22, "분석범위 Buffer 생성 중")
        aoi = create_aoi_layer(buffer_source, config.buffer_distance)
        report(27, "선형·분석범위 Geometry 및 좌표계 검증 중")
        issues = cad_warnings + validate_route_and_aoi(buffer_source, aoi) + korea_sanity_issues(transformed_layer)
        if buffer_source is not transformed_layer:
            issues = validate_layer(transformed_layer) + issues
        if any(issue.severity == "error" for issue in issues):
            raise RouteGISError("노선 또는 분석 범위 자동 검증에 실패했습니다.")

        definitions = self.catalog.select(config.selected_layers)
        downloads: list[DownloadResult] = []
        report(30, f"공간정보 {len(definitions)}개 수집 시작")
        if definitions:
            owned_providers: dict[str, SpatialDataProvider] = {}
            provider_failures: dict[str, str] = {}
            try:
                for index, definition in enumerate(definitions, start=1):
                    label = f"[{index}/{len(definitions)}] {definition.name} [{definition.provider}]"
                    report(
                        30 + 45 * (index - 1) // len(definitions),
                        f"{label} 수집 중 · 서버 응답/재시도 대기",
                    )
                    try:
                        if provider is not None:
                            active_provider = provider
                        else:
                            previous_failure = provider_failures.get(definition.provider)
                            if previous_failure is not None:
                                raise ProviderError(previous_failure)
                            active_provider = owned_providers.get(definition.provider)
                            if active_provider is None:
                                try:
                                    active_provider = self._provider(
                                        definition.provider,
                                        api_key=api_key,
                                        data_go_kr_service_key=data_go_kr_service_key,
                                        domain=domain if definition.provider == "vworld" else None,
                                        on_progress=lambda message: report(
                                            30 + 45 * (index - 1) // len(definitions),
                                            f"{label} · {message}",
                                        ),
                                    )
                                except Exception as exc:
                                    provider_failures[definition.provider] = str(exc)
                                    raise
                                owned_providers[definition.provider] = active_provider
                        result = active_provider.fetch_layer(definition, aoi)
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
                            log.warning(
                                "레이어 다운로드 실패: %s: %s",
                                definition.id,
                                result.error,
                            )
                        elif result.layer is None:
                            result.status = "failed"
                            result.feature_count = 0
                            result.error = "공급자가 완전한 레이어를 반환하지 않았습니다."
                            log.warning(
                                "레이어 다운로드 실패: %s: %s",
                                definition.id,
                                result.error,
                            )
                        else:
                            result.feature_count = len(result.layer.features)
                            layer_issues = _provider_contract_issues(
                                definition,
                                result.layer,
                                aoi,
                            )
                            if not any(
                                issue.severity == "error"
                                for issue in layer_issues
                            ):
                                layer_issues += validate_layer(
                                    result.layer
                                ) + validate_layer_intersects_aoi(
                                    result.layer,
                                    aoi,
                                )
                            issues.extend(layer_issues)
                            if any(issue.severity == "error" for issue in layer_issues):
                                result.status = "failed"
                                result.error = "Geometry 또는 좌표계 검증에 실패했습니다."
                                result.layer = None
                                result.feature_count = 0
                        downloads.append(result)
                    except Exception as exc:
                        log.warning("레이어 다운로드 실패: %s: %s", definition.id, exc)
                        downloads.append(
                            DownloadResult(
                                layer_id=definition.id,
                                status="failed",
                                error=_redact(str(exc), *secrets),
                            )
                        )
                    result = downloads[-1]
                    detail = (
                        f"성공 · {result.feature_count}개"
                        if result.status == "success"
                        else f"실패 · {result.error or '상세 오류 없음'}"
                    )
                    report(30 + 45 * index // len(definitions), f"{label} {detail}")
            finally:
                for owned_provider in owned_providers.values():
                    close = getattr(owned_provider, "close", None)
                    if callable(close):
                        try:
                            close()
                        except Exception as exc:
                            log.warning("공급자 종료 실패: %s", exc)

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
        layers = [transformed_layer, aoi, *successful_layers]
        report(78, "GeoPackage 및 Shapefile 저장 중")
        geopackage, shapefile_mappings = export_layers(
            project_directory,
            layers,
            export_shapefiles=config.export_shapefile,
            layer_metadata=layer_metadata,
        )
        if config.export_shapefile:
            for layer in layers:
                if layer.name not in shapefile_mappings:
                    issues.append(
                        ValidationIssue(
                            severity="warning",
                            code="SHAPEFILE_ENCODING_FALLBACK",
                            message=(
                                "EUC-KR로 표현할 수 없는 속성값이 있어 Shapefile 대신 "
                                "GeoPackage를 사용합니다."
                            ),
                            layer=layer.name,
                        )
                    )
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
            use_native=use_native_project,
        )
        state = (
            PipelineState.COMPLETED_WITH_WARNINGS
            if any(issue.severity == "warning" for issue in issues)
            or any(result.status != "success" for result in downloads)
            else PipelineState.COMPLETED
        )
        report(95, "Manifest 및 처리 보고서 작성 중")
        manifest_payload = {
            "project": config.project_name,
            "project_type": config.project_type,
            "status": state.value,
            "source": {
                "filename": config.source_drawing.name,
                "sha256": source_digest,
                "layers": list(route.source_layers),
                "dwg_converter": config.dwg_converter.name if config.dwg_converter else None,
            },
            "route": {
                "source_crs": config.source_crs,
                "output_crs": config.output_crs,
                "buffer": config.buffer_distance,
                "buffer_source_layers": list(buffer_source_layers),
                "length_m": route.length,
                "bbox": route.bbox,
                "aoi_bbox": aoi.bounds,
                "snap_tolerance": config.snap_tolerance,
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
            },
        }
        manifest = project_directory / "project_manifest.json"
        manifest.write_text(json.dumps(manifest_payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        report_payload = {
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
            "outputs": ["project.gpkg", project_file.name, "project_manifest.json"],
        }
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
        )
