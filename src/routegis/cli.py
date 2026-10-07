from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from shapely.geometry import shape

from .app.project_service import ProjectService
from .config import load_profile, load_settings
from .environment import load_data_go_kr_service_key, load_vworld_api_key
from .errors import RouteGISError
from .models import DownloadResult, LayerDefinition, ProjectConfig, SpatialFeature, SpatialLayer
from .security import redact_sensitive_text


def _redact_error(message: object, *secrets: str) -> str:
    redacted = redact_sensitive_text(message, *secrets) or ""
    redacted = redacted.replace(str(Path.cwd() / ".env"), "[dotenv]")
    return redacted


class FixtureProvider:
    """Offline provider used by the documented golden acceptance workflow."""

    def __init__(self, path: Path):
        with path.open("r", encoding="utf-8") as stream:
            payload = json.load(stream)
        if not isinstance(payload, dict) or not isinstance(payload.get("layers"), dict):
            raise RouteGISError("오프라인 Fixture 형식이 올바르지 않습니다.")
        self.crs = str(payload.get("crs", "EPSG:5186"))
        self.layers: dict[str, Any] = payload["layers"]

    def fetch_layer(self, definition: LayerDefinition, aoi: SpatialLayer) -> DownloadResult:
        payload = self.layers.get(definition.id)
        if payload is None:
            return DownloadResult(definition.id, "failed", error="오프라인 Fixture에 레이어가 없습니다.")
        if isinstance(payload, dict) and payload.get("error"):
            return DownloadResult(definition.id, "failed", error=str(payload["error"]))
        features = payload.get("features") if isinstance(payload, dict) else None
        if not isinstance(features, list):
            return DownloadResult(definition.id, "failed", error="오프라인 FeatureCollection이 올바르지 않습니다.")
        parsed = []
        for index, feature in enumerate(features):
            if not isinstance(feature, dict) or not isinstance(feature.get("geometry"), dict):
                continue
            geometry = shape(feature["geometry"])
            if geometry.intersects(aoi.features[0].geometry):
                parsed.append(
                    SpatialFeature(
                        geometry,
                        dict(feature.get("properties") or {}),
                        str(feature.get("id", f"fixture-{index}")),
                    )
                )
        layer = SpatialLayer(
            name=definition.id,
            display_name=definition.name,
            crs=self.crs,
            geometry_type=definition.geometry_type,
            category=definition.category,
            features=parsed,
            source="fixture",
        )
        return DownloadResult(definition.id, "success", len(parsed), layer)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="routegis",
        description="노선도면으로 QGIS 사업분석 프로젝트를 생성합니다.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    analyze = subparsers.add_parser("analyze", help="DXF 레이어와 노선 후보를 분석합니다.")
    analyze.add_argument("drawing", type=Path, help="분석할 DXF 파일")
    analyze.add_argument("--chord-tolerance", type=float, default=None, help="곡선 선형화 허용오차")

    build = subparsers.add_parser("build", help="RouteGIS 프로젝트를 생성합니다.")
    build.add_argument("drawing", type=Path, help="DWG 또는 DXF 노선도면")
    build.add_argument("--project-name", required=True, help="사업명")
    build.add_argument("--output", type=Path, required=True, help="프로젝트를 생성할 상위 폴더")
    build.add_argument("--route-layer", default="", help="선형 사업에서 사용할 쉼표 구분 노선 CAD 레이어")
    build.add_argument(
        "--project-type",
        choices=("linear", "non_linear"),
        default="linear",
        help="사업 유형 (기본: linear)",
    )
    build.add_argument("--source-crs", required=True, help="사용자가 확인한 원본 좌표계 (예: EPSG:5186)")
    build.add_argument("--output-crs", default=None, help="결과 좌표계 (기본: config.yaml)")
    build.add_argument("--buffer", type=float, default=None, help="사업구간 주변 범위(m)")
    build.add_argument("--profile", default="basic_route", help="레이어 프로필 ID")
    build.add_argument("--layers", help="프로필 대신 사용할 쉼표 구분 레이어 ID")
    build.add_argument("--domain", help="VWorld API Key 등록 도메인")
    build.add_argument("--api-key-env", default="VWORLD_API_KEY", help="API Key를 읽을 환경변수명")
    build.add_argument("--dwg-converter", type=Path, help="외부 DWG→DXF 변환기")
    build.add_argument("--fixture", type=Path, help="실제 API 대신 사용할 오프라인 GeoJSON Fixture")
    build.add_argument("--no-shp", action="store_true", help="호환용 Shapefile 생성을 생략합니다.")
    return parser


def _run_analyze(args: argparse.Namespace) -> int:
    candidates = ProjectService().analyze_drawing(args.drawing, chord_tolerance=args.chord_tolerance)
    print(json.dumps(candidates, ensure_ascii=False, indent=2))
    return 0


def _run_build(args: argparse.Namespace) -> int:
    settings = load_settings()
    if args.layers is None:
        layers = load_profile(args.profile)
    else:
        layers = tuple(item.strip() for item in args.layers.split(",") if item.strip())
    config = ProjectConfig(
        project_name=args.project_name,
        source_drawing=args.drawing.resolve(),
        source_layers=tuple(item.strip() for item in args.route_layer.split(",") if item.strip()),
        source_crs=args.source_crs.upper(),
        output_crs=(args.output_crs or settings.default_output_crs).upper(),
        buffer_distance=(
            settings.default_buffer if args.buffer is None else args.buffer
        ),
        selected_layers=layers,
        output_directory=args.output.resolve(),
        export_shapefile=not args.no_shp,
        snap_tolerance=settings.snap_tolerance,
        chord_tolerance=settings.chord_tolerance,
        dwg_converter=args.dwg_converter.resolve() if args.dwg_converter else None,
        project_type=args.project_type,
    )
    provider = FixtureProvider(args.fixture) if args.fixture else None
    api_key = ""
    data_go_kr_service_key = ""
    try:
        if provider is None and args.api_key_env == "VWORLD_API_KEY":
            api_key = load_vworld_api_key(Path.cwd() / ".env")
        elif provider is None:
            api_key = os.getenv(args.api_key_env, "").strip()
        if provider is None:
            data_go_kr_service_key = load_data_go_kr_service_key(Path.cwd() / ".env")
        result = ProjectService(settings).build(
            config,
            api_key=api_key,
            data_go_kr_service_key=data_go_kr_service_key,
            domain=args.domain,
            provider=provider,
        )
    except (RouteGISError, OSError, ValueError) as exc:
        raise RouteGISError(
            _redact_error(exc, api_key, data_go_kr_service_key)
        ) from exc
    print(
        json.dumps(
            {
                "status": result.state.value,
                "project_directory": str(result.project_directory),
                "project_file": str(result.project_file),
                "geopackage": str(result.geopackage),
                "report": str(result.report_html),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "analyze":
            return _run_analyze(args)
        return _run_build(args)
    except (RouteGISError, OSError, ValueError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
