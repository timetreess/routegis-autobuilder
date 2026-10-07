from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .errors import ConfigurationError
from .models import LayerDefinition, ProjectConfig


EPSG_PATTERN = re.compile(r"^EPSG:\d{4,6}$", re.IGNORECASE)
SAFE_NAME_PATTERN = re.compile(r"[^0-9A-Za-z가-힣._-]+")


@dataclass(frozen=True)
class AppSettings:
    default_buffer: float
    default_output_crs: str
    vworld_timeout: float
    vworld_retry: int
    vworld_backoff: float
    vworld_page_size: int
    download_request_budget_minimum: int
    download_request_budget_per_tile: int
    download_request_budget_maximum: int
    download_max_split_depth: int
    download_minimum_tile_size_m: float
    download_max_response_bytes: int
    vworld_land_ownership_tile_size_m: float
    vworld_default_tile_size_m: float
    ecology_tile_size_m: float
    ecology_timeout: float
    ecology_retry: int
    ecology_page_size: int
    osm_endpoint: str
    osm_tile_size_m: float
    osm_timeout: float
    osm_retry: int
    snap_tolerance: float
    chord_tolerance: float
    export_geopackage: bool
    export_shapefile: bool
    qgis_template_version: str


def resource_root() -> Path:
    return Path(__file__).resolve().parent / "resources"


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigurationError(f"설정 파일을 찾을 수 없습니다: {path}")
    try:
        with path.open("r", encoding="utf-8") as stream:
            data = yaml.safe_load(stream)
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"설정 파일을 읽을 수 없습니다: {path}") from exc
    if not isinstance(data, dict):
        raise ConfigurationError(f"설정 파일 형식이 올바르지 않습니다: {path}")
    return data


def load_settings(path: Path | None = None) -> AppSettings:
    data = _read_yaml(path or resource_root() / "config.yaml")
    try:
        return AppSettings(
            default_buffer=float(data["project"]["default_buffer"]),
            default_output_crs=str(data["project"]["output_crs"]),
            vworld_timeout=float(data["vworld"]["timeout"]),
            vworld_retry=int(data["vworld"]["retry"]),
            vworld_backoff=float(data["vworld"]["backoff"]),
            vworld_page_size=int(data["vworld"]["page_size"]),
            download_request_budget_minimum=int(data["download"]["request_budget_minimum"]),
            download_request_budget_per_tile=int(data["download"]["request_budget_per_tile"]),
            download_request_budget_maximum=int(data["download"]["request_budget_maximum"]),
            download_max_split_depth=int(data["download"]["max_split_depth"]),
            download_minimum_tile_size_m=float(data["download"]["minimum_tile_size_m"]),
            download_max_response_bytes=int(data["download"]["max_response_bytes"]),
            vworld_land_ownership_tile_size_m=float(data["vworld"]["land_ownership_tile_size_m"]),
            vworld_default_tile_size_m=float(data["vworld"]["default_tile_size_m"]),
            ecology_tile_size_m=float(data["ecology"]["tile_size_m"]),
            ecology_timeout=float(data["ecology"]["timeout"]),
            ecology_retry=int(data["ecology"]["retry"]),
            ecology_page_size=int(data["ecology"]["page_size"]),
            osm_endpoint=str(data["osm"]["endpoint"]),
            osm_tile_size_m=float(data["osm"]["tile_size_m"]),
            osm_timeout=float(data["osm"]["timeout"]),
            osm_retry=int(data["osm"]["retry"]),
            snap_tolerance=float(data["cad"]["snap_tolerance"]),
            chord_tolerance=float(data["cad"]["chord_tolerance"]),
            export_geopackage=bool(data["export"]["geopackage"]),
            export_shapefile=bool(data["export"]["shapefile"]),
            qgis_template_version=str(data["qgis"]["template_version"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigurationError(f"필수 설정값이 없거나 잘못되었습니다: {exc}") from exc


def load_layer_catalog(path: Path | None = None) -> dict[str, LayerDefinition]:
    data = _read_yaml(path or resource_root() / "layer_catalog.yaml")
    raw_layers = data.get("layers")
    if not isinstance(raw_layers, dict):
        raise ConfigurationError("레이어 카탈로그에 'layers' 항목이 없습니다.")
    catalog: dict[str, LayerDefinition] = {}
    for layer_id, raw in raw_layers.items():
        if not isinstance(raw, dict):
            raise ConfigurationError(f"레이어 정의가 올바르지 않습니다: {layer_id}")
        fields = dict(raw)
        aliases = fields.get("aliases", [])
        if not isinstance(aliases, list) or not all(
            isinstance(alias, str) and alias.strip() for alias in aliases
        ):
            raise ConfigurationError(f"레이어 별칭이 올바르지 않습니다: {layer_id}")
        fields["aliases"] = tuple(aliases)
        try:
            definition = LayerDefinition(id=str(layer_id), **fields)
        except TypeError as exc:
            raise ConfigurationError(f"레이어 정의 필드가 잘못되었습니다: {layer_id}") from exc
        if definition.service_type not in {"wfs", "data", "overpass"}:
            raise ConfigurationError(f"지원하지 않는 서비스 유형입니다: {definition.service_type}")
        if not EPSG_PATTERN.fullmatch(definition.request_crs):
            raise ConfigurationError(f"레이어 요청 좌표계가 잘못되었습니다: {definition.request_crs}")
        if definition.response_crs and not EPSG_PATTERN.fullmatch(definition.response_crs):
            raise ConfigurationError(f"레이어 응답 좌표계가 잘못되었습니다: {definition.response_crs}")
        catalog[definition.id] = definition
    return catalog


def load_profile(profile_id: str = "basic_route", path: Path | None = None) -> tuple[str, ...]:
    profile_path = path or resource_root() / "presets" / f"{profile_id}.yaml"
    data = _read_yaml(profile_path)
    layers = data.get("layers")
    if not isinstance(layers, list) or not all(isinstance(item, str) for item in layers):
        raise ConfigurationError(f"레이어 프로필 형식이 올바르지 않습니다: {profile_path}")
    return tuple(layers)


def safe_project_name(name: str) -> str:
    safe = SAFE_NAME_PATTERN.sub("_", name.strip()).strip("._")
    if not safe:
        raise ConfigurationError("사업명을 입력해 주세요.")
    return safe


def validate_project_config(config: ProjectConfig, catalog: dict[str, LayerDefinition]) -> None:
    safe_project_name(config.project_name)
    if not config.source_drawing.is_file():
        raise ConfigurationError(f"도면 파일을 찾을 수 없습니다: {config.source_drawing}")
    if config.source_drawing.suffix.lower() not in {".dxf", ".dwg"}:
        raise ConfigurationError("도면 파일은 DWG 또는 DXF 형식이어야 합니다.")
    if config.project_type not in {"linear", "non_linear"}:
        raise ConfigurationError(f"지원하지 않는 사업 유형입니다: {config.project_type}")
    if config.project_type == "linear" and (
        not config.source_layers or any(not layer.strip() for layer in config.source_layers)
    ):
        raise ConfigurationError("노선 레이어를 선택해 주세요.")
    if len(set(config.source_layers)) != len(config.source_layers):
        raise ConfigurationError("노선 레이어가 중복 선택되었습니다.")
    if len(set(config.selected_layers)) != len(config.selected_layers):
        raise ConfigurationError("공간정보가 중복 선택되었습니다.")
    for crs in (config.source_crs, config.output_crs):
        if not EPSG_PATTERN.fullmatch(crs):
            raise ConfigurationError(f"좌표계 형식이 올바르지 않습니다: {crs}")
    if config.buffer_distance <= 0:
        raise ConfigurationError("사업구간 주변 범위는 0보다 커야 합니다.")
    if config.snap_tolerance <= 0 or config.chord_tolerance <= 0:
        raise ConfigurationError("도면 연결 및 곡선 허용오차는 0보다 커야 합니다.")
    unknown = sorted(set(config.selected_layers) - set(catalog))
    if unknown:
        raise ConfigurationError(f"카탈로그에 없는 공간정보입니다: {', '.join(unknown)}")
    if config.source_drawing.suffix.lower() == ".dwg" and config.dwg_converter is None:
        raise ConfigurationError("DWG 파일을 사용하려면 외부 DWG→DXF 변환기를 지정해 주세요.")
