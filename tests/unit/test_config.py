from __future__ import annotations

from dataclasses import FrozenInstanceError, asdict
from inspect import Parameter, signature
from pathlib import Path
from typing import Any

import pytest
import yaml

from routegis.config import load_layer_catalog, load_settings, validate_project_config
from routegis.errors import ConfigurationError
from routegis.models import DownloadResult, LayerDefinition, ProjectConfig


def _layer_fields() -> dict[str, Any]:
    return {
        "id": "protected_area",
        "name": "Protected area",
        "provider": "ecology",
        "service_type": "wfs",
        "endpoint": "https://example.test/wfs",
        "data_id": "protected_area",
        "geometry_type": "MultiPolygon",
        "category": "ecology",
        "request_crs": "EPSG:5186",
        "publisher": "National Institute of Ecology",
        "dataset_id": "ecology-protected-area",
        "license_name": "Public Data License",
        "license_url": "https://example.test/license",
    }


@pytest.mark.parametrize("field_name", ["publisher", "dataset_id", "license_name", "license_url"])
def test_layer_definition_requires_source_metadata(field_name: str):
    parameter = signature(LayerDefinition).parameters.get(field_name)
    fields = _layer_fields()
    del fields[field_name]

    assert parameter is not None
    assert parameter.default is Parameter.empty
    with pytest.raises(TypeError, match=field_name):
        LayerDefinition(**fields)


def test_layer_definition_aliases_are_optional_and_the_model_is_frozen():
    definition = LayerDefinition(**_layer_fields())
    aliased = LayerDefinition(**_layer_fields(), aliases=("보호지역", "생태보호구역"))

    assert definition.aliases == ()
    assert aliased.aliases == ("보호지역", "생태보호구역")
    with pytest.raises(FrozenInstanceError):
        setattr(definition, "name", "Changed")


def test_download_result_tile_statistics_default_to_empty():
    result = DownloadResult(layer_id="protected_area", status="success")

    assert result.initial_tile_count == 0
    assert result.request_count == 0
    assert result.successful_tile_count == 0
    assert result.failed_tile_count == 0
    assert result.failed_tile_bbox is None


def _catalog_path(
    tmp_path: Path,
    *,
    service_type: str = "wfs",
    missing_metadata: str | None = None,
    aliases: object = None,
    omit_aliases: bool = False,
) -> Path:
    layer: dict[str, Any] = {
        "name": "Protected area",
        "provider": "ecology",
        "service_type": service_type,
        "endpoint": "https://example.test/wfs",
        "data_id": "protected_area",
        "geometry_type": "MultiPolygon",
        "category": "ecology",
        "request_crs": "EPSG:5186",
        "publisher": "National Institute of Ecology",
        "dataset_id": "ecology-protected-area",
        "license_name": "Public Data License",
        "license_url": "https://example.test/license",
    }
    if not omit_aliases:
        layer["aliases"] = ["보호지역", "생태보호구역"] if aliases is None else aliases
    if missing_metadata is not None:
        del layer[missing_metadata]
    path = tmp_path / "catalog.yaml"
    path.write_text(
        yaml.safe_dump({"layers": {"protected_area": layer}}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize("service_type", ["wfs", "data", "overpass"])
def test_load_layer_catalog_accepts_supported_service_types_and_reads_aliases(
    tmp_path: Path,
    service_type: str,
):
    definition = load_layer_catalog(_catalog_path(tmp_path, service_type=service_type))["protected_area"]

    assert definition.service_type == service_type
    assert definition.aliases == ("보호지역", "생태보호구역")


def test_load_layer_catalog_defaults_omitted_aliases_to_empty_tuple(tmp_path: Path):
    definition = load_layer_catalog(_catalog_path(tmp_path, omit_aliases=True))["protected_area"]

    assert definition.aliases == ()


@pytest.mark.parametrize(
    "aliases",
    [
        pytest.param("보호지역", id="scalar"),
        pytest.param({"primary": "보호지역"}, id="mapping"),
        pytest.param(["보호지역", "  "], id="blank-string"),
        pytest.param(["보호지역", 7], id="non-string"),
    ],
)
def test_load_layer_catalog_rejects_malformed_aliases(tmp_path: Path, aliases: object):
    with pytest.raises(ConfigurationError, match="protected_area"):
        load_layer_catalog(_catalog_path(tmp_path, aliases=aliases))


def test_load_layer_catalog_rejects_unsupported_service_type(tmp_path: Path):
    with pytest.raises(ConfigurationError, match="지원하지 않는 서비스 유형"):
        load_layer_catalog(_catalog_path(tmp_path, service_type="wmts"))


@pytest.mark.parametrize("field_name", ["publisher", "dataset_id", "license_name", "license_url"])
def test_load_layer_catalog_requires_source_metadata(tmp_path: Path, field_name: str):
    with pytest.raises(ConfigurationError, match="레이어 정의 필드"):
        load_layer_catalog(_catalog_path(tmp_path, missing_metadata=field_name))


def test_invalid_yaml_is_reported_as_configuration_error(tmp_path: Path):
    path = tmp_path / "invalid.yaml"
    path.write_text("project: [", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="설정 파일을 읽을 수 없습니다"):
        load_settings(path)


def test_project_config_rejects_duplicate_selected_layers(tmp_path: Path):
    drawing = tmp_path / "route.dxf"
    drawing.touch()
    config = ProjectConfig(
        project_name="중복 레이어",
        source_drawing=drawing,
        source_layers=("CENTER",),
        source_crs="EPSG:5186",
        output_crs="EPSG:5186",
        buffer_distance=1000,
        selected_layers=("protected_area", "protected_area"),
        output_directory=tmp_path,
    )

    with pytest.raises(ConfigurationError, match="공간정보가 중복 선택"):
        validate_project_config(
            config,
            {"protected_area": LayerDefinition(**_layer_fields())},
        )


def test_default_settings_match_the_approved_multi_provider_values():
    assert asdict(load_settings()) == {
        "default_buffer": 1000.0,
        "default_output_crs": "EPSG:5186",
        "vworld_timeout": 30.0,
        "vworld_retry": 3,
        "vworld_backoff": 0.5,
        "vworld_page_size": 1000,
        "download_request_budget_minimum": 100,
        "download_request_budget_per_tile": 16,
        "download_request_budget_maximum": 5000,
        "download_max_split_depth": 32,
        "download_minimum_tile_size_m": 50.0,
        "download_max_response_bytes": 67108864,
        "vworld_land_ownership_tile_size_m": 5000.0,
        "vworld_default_tile_size_m": 10000.0,
        "ecology_tile_size_m": 10000.0,
        "ecology_timeout": 30.0,
        "ecology_retry": 3,
        "ecology_page_size": 500,
        "osm_endpoint": "https://overpass-api.de/api/interpreter",
        "osm_tile_size_m": 25000.0,
        "osm_timeout": 180.0,
        "osm_retry": 2,
        "snap_tolerance": 0.1,
        "chord_tolerance": 0.1,
        "export_geopackage": True,
        "export_shapefile": True,
        "qgis_template_version": "3.40.0",
    }
