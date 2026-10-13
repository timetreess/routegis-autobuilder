"""Catalog search, tooltip, and default-selection behavior used by the Wizard."""

from __future__ import annotations

from dataclasses import asdict, replace
from pathlib import Path

import httpx
import pytest

from routegis.config import load_profile
from routegis.errors import ConfigurationError
from routegis.models import LayerDefinition
from routegis.providers.catalog import LayerCatalog, layer_tooltip_lines
from routegis.providers.vworld.client import VWorldClient
from routegis.providers.vworld.taxonomy import TaxonomyEntry


VWORLD_PUBLISHER = "국토교통부 공간정보 오픈플랫폼(VWorld)"
VWORLD_LICENSE = "VWorld OpenAPI 이용조건"
VWORLD_LICENSE_URL = "https://www.vworld.kr/dev/v4dv_wmsguide2_s001.do"
OSM_PUBLISHER = "OpenStreetMap contributors"
OSM_LICENSE = "Open Database License (ODbL) 1.0"
OSM_LICENSE_URL = "https://www.openstreetmap.org/copyright"
DEFAULT_LAYER_IDS = (
    "ecology_nature",
    "land_ownership",
    "heritage_protection",
    "power_line",
    "power_support",
    "river_area",
)
LEGACY_LAYER_IDS = {
    "cadastral",
    "administrative_sido",
    "administrative_sigungu",
    "administrative_emd",
    "landuse_zone",
    "road",
    "building",
    "river_centerline",
}


def _live_layer(data_id: str, name: str, *, layer_id: str | None = None) -> LayerDefinition:
    return LayerDefinition(
        id=layer_id or data_id,
        name=name,
        provider="vworld",
        service_type="wfs",
        endpoint="https://api.vworld.kr/req/wfs",
        data_id=data_id,
        geometry_type="Unknown",
        category="facility",
        request_crs="EPSG:4326",
        response_crs="EPSG:4326",
        publisher=VWORLD_PUBLISHER,
        dataset_id=data_id,
        license_name=VWORLD_LICENSE,
        license_url=VWORLD_LICENSE_URL,
    )


@pytest.mark.parametrize(
    "query",
    [
        pytest.param("ecology", id="provider"),
        pytest.param("국립생태원", id="publisher"),
        pytest.param("15057288", id="dataset-id"),
        pytest.param("생태등급", id="alias"),
        pytest.param("ECOLOGY_NATURE", id="routegis-id"),
        pytest.param("생태자연도", id="display-name"),
        pytest.param("TBL_OPN_ECZM", id="data-id"),
    ],
)
def test_layer_search_matches_every_general_catalog_dimension(query: str):
    catalog = LayerCatalog()
    ecology = catalog.get("ecology_nature")

    assert catalog.matches(ecology, query)
    assert not catalog.matches(catalog.get("cadastral"), query)


def test_live_catalog_merge_adds_only_unique_vworld_layers_and_preserves_fixed_entries():
    catalog = LayerCatalog()
    fixed_ids = {
        "ecology_nature",
        "power_line",
        "power_support",
        "river_centerline",
        "river_area",
    }
    fixed_before = {layer_id: catalog.get(layer_id) for layer_id in fixed_ids}
    duplicate_data_id = _live_layer(
        "LP_PA_CBND_BUBUN",
        "실시간 지적도",
        layer_id="live_cadastral_duplicate",
    )
    duplicate_routegis_id = _live_layer(
        "unique_live_data_id",
        "중복 RouteGIS ID",
        layer_id="CADASTRAL",
    )
    fault = _live_layer("lt_l_gimsfault", "단층")
    non_vworld = replace(
        _live_layer("foreign_live", "외부 실시간 레이어"),
        provider="osm",
        publisher=OSM_PUBLISHER,
        dataset_id="openstreetmap",
        license_name=OSM_LICENSE,
        license_url=OSM_LICENSE_URL,
    )

    added = catalog.merge_live(
        [duplicate_data_id, duplicate_routegis_id, fault, non_vworld]
    )

    assert added == 1
    assert catalog.get("lt_l_gimsfault") == fault
    assert catalog.get("administrative_sido").service_type == "data"
    assert {layer_id: catalog.get(layer_id) for layer_id in fixed_ids} == fixed_before
    for rejected_id in ("live_cadastral_duplicate", "CADASTRAL", "foreign_live"):
        with pytest.raises(ConfigurationError):
            catalog.get(rejected_id)


def test_catalog_snapshot_cannot_mutate_active_catalog():
    catalog = LayerCatalog()
    snapshot = catalog.snapshot()

    snapshot.pop("cadastral")

    assert catalog.get("cadastral").name == "연속지적도"


def test_non_vworld_definition_never_receives_vworld_taxonomy(fixture_directory: Path):
    catalog = LayerCatalog(taxonomy_path=fixture_directory / "vworld_taxonomy.yaml")
    vworld = _live_layer("lt_c_uo301", "분류 대상")
    non_vworld = replace(
        vworld,
        id="non_vworld_heritage",
        name="분류 격리",
        provider="osm",
        aliases=(),
        publisher=OSM_PUBLISHER,
        dataset_id="openstreetmap",
        license_name=OSM_LICENSE,
        license_url=OSM_LICENSE_URL,
    )

    assert catalog.taxonomy_for(vworld) is not None
    assert catalog.taxonomy_for(non_vworld) is None
    assert not catalog.matches(non_vworld, "문화재")


def test_heritage_category_aliases_match_all_loaded_members(fixture_directory: Path):
    catalog = LayerCatalog(taxonomy_path=fixture_directory / "vworld_taxonomy.yaml")
    heritage = [
        catalog.get("heritage_protection"),
        _live_layer("lt_p_dgmuseumart", "박물관미술관"),
        _live_layer("lt_c_uo501", "전통사찰보존"),
    ]
    catalog.merge_live(heritage[1:])
    expected = {definition.id for definition in heritage}

    for query in ("문화재", "문화유산", "국가유산"):
        matched = {
            definition.id
            for definition in catalog.all()
            if catalog.matches(definition, query)
        }
        assert expected <= matched
        assert all(
            catalog.taxonomy_for(catalog.get(layer_id)) is not None
            for layer_id in expected
        )


def test_taxonomy_search_uses_official_name_without_replacing_live_name(
    fixture_directory: Path,
):
    catalog = LayerCatalog(taxonomy_path=fixture_directory / "vworld_taxonomy.yaml")
    definition = _live_layer("lt_p_dgmuseumart", "실시간 박물관 제목")
    before = asdict(definition)
    catalog.merge_live([definition])

    assert catalog.matches(definition, "박물관미술관")
    assert catalog.matches(definition, "시설") is False
    assert catalog.get(definition.id).name == "실시간 박물관 제목"
    assert asdict(catalog.get(definition.id)) == before


def test_taxonomy_does_not_materialize_absent_layers(fixture_directory: Path):
    catalog = LayerCatalog(taxonomy_path=fixture_directory / "vworld_taxonomy.yaml")
    before = {definition.id for definition in catalog.all()}

    assert "lt_p_dgmuseumart" not in before
    assert {definition.id for definition in catalog.all()} == before
    with pytest.raises(ConfigurationError):
        catalog.get("lt_p_dgmuseumart")


def test_unclassified_live_layer_remains_unchanged_searchable_and_sorts_last(
    fixture_directory: Path,
):
    catalog = LayerCatalog(taxonomy_path=fixture_directory / "vworld_taxonomy.yaml")
    definition = _live_layer("custom_unclassified", "새 실시간 레이어")
    before = asdict(definition)
    catalog.merge_live([definition])

    assert catalog.taxonomy_for(definition) is None
    assert catalog.matches(definition, "새 실시간")
    assert catalog.matches(definition, "CUSTOM_UNCLASSIFIED")
    assert catalog.sort_key(definition) == (
        1,
        "",
        "새 실시간 레이어".casefold(),
        "custom_unclassified",
        "custom_unclassified",
    )
    assert asdict(catalog.get(definition.id)) == before


def test_taxonomy_attaches_by_data_id_while_yaml_definition_keeps_precedence(
    fixture_directory: Path,
):
    catalog = LayerCatalog(taxonomy_path=fixture_directory / "vworld_taxonomy.yaml")
    duplicate = _live_layer("LP_PA_CBND_BUBUN", "실시간 지적도")
    added = catalog.merge_live([duplicate])
    definition = catalog.get("cadastral")

    assert added == 0
    assert definition.name == "연속지적도"
    taxonomy = catalog.taxonomy_for(definition)
    assert taxonomy is not None
    assert taxonomy.reference_category == "토지"


def test_sort_key_is_deterministic_for_classified_layers(fixture_directory: Path):
    catalog = LayerCatalog(taxonomy_path=fixture_directory / "vworld_taxonomy.yaml")
    definition = _live_layer("lt_p_dgmuseumart", "박물관미술관")

    assert catalog.sort_key(definition) == (
        0,
        "국가유산".casefold(),
        "박물관미술관".casefold(),
        "lt_p_dgmuseumart",
        "lt_p_dgmuseumart",
    )


def test_discovered_vworld_layers_have_complete_source_metadata():
    capabilities = b"""<?xml version="1.0" encoding="UTF-8"?>
    <WFS_Capabilities>
      <FeatureTypeList>
        <FeatureType><Name>lt_c_uo301</Name><Title>Heritage</Title></FeatureType>
        <FeatureType><Name>dt_d160</Name><Title>Ownership</Title></FeatureType>
      </FeatureTypeList>
    </WFS_Capabilities>
    """

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=capabilities)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        definitions = VWorldClient(
            "secret-key",
            retries=0,
            client=http_client,
        ).discover_layers()

    assert {definition.dataset_id for definition in definitions} == {
        "lt_c_uo301",
        "dt_d160",
    }
    assert all(
        (
            definition.publisher,
            definition.dataset_id,
            definition.license_name,
            definition.license_url,
        )
        == (
            VWORLD_PUBLISHER,
            definition.data_id,
            VWORLD_LICENSE,
            VWORLD_LICENSE_URL,
        )
        for definition in definitions
    )


def test_basic_route_contains_six_layers_without_optional_river_centerline():
    assert load_profile() == DEFAULT_LAYER_IDS


def test_legacy_layers_remain_available_but_are_not_selected_by_default():
    catalog_ids = {definition.id for definition in LayerCatalog().all()}

    assert catalog_ids == LEGACY_LAYER_IDS | set(DEFAULT_LAYER_IDS)
    assert LEGACY_LAYER_IDS.isdisjoint(load_profile())


def test_packaged_default_layer_definitions_match_the_approved_schema():
    catalog = LayerCatalog()
    expected = {
        "ecology_nature": {
            "name": "생태자연도",
            "provider": "ecology",
            "service_type": "wfs",
            "endpoint": "https://apis.data.go.kr/B553084/ecoapi/EcologyzmpService/wfs/getEcologyzmpWFS",
            "data_id": "tbl_opn_eczm",
            "geometry_type": "MultiPolygon",
            "category": "ecology",
            "request_crs": "EPSG:5186",
            "response_crs": "EPSG:5186",
            "publisher": "국립생태원",
            "dataset_id": "15057288",
            "license_name": "이용허락범위 제한 없음",
            "license_url": "https://www.data.go.kr/data/15057288/openapi.do",
            "aliases": ("생태 자연도", "생태등급"),
        },
        "land_ownership": {
            "name": "토지소유공간정보",
            "provider": "vworld",
            "service_type": "wfs",
            "endpoint": "https://api.vworld.kr/req/wfs",
            "data_id": "dt_d160",
            "geometry_type": "MultiPolygon",
            "category": "land",
            "request_crs": "EPSG:4326",
            "response_crs": "EPSG:4326",
            "publisher": VWORLD_PUBLISHER,
            "dataset_id": "dt_d160",
            "license_name": VWORLD_LICENSE,
            "license_url": VWORLD_LICENSE_URL,
            "aliases": (),
        },
        "heritage_protection": {
            "name": "국가유산 지정·보호구역",
            "provider": "vworld",
            "service_type": "wfs",
            "endpoint": "https://api.vworld.kr/req/wfs",
            "data_id": "lt_c_uo301",
            "geometry_type": "MultiPolygon",
            "category": "heritage",
            "request_crs": "EPSG:4326",
            "response_crs": "EPSG:4326",
            "publisher": VWORLD_PUBLISHER,
            "dataset_id": "lt_c_uo301",
            "license_name": VWORLD_LICENSE,
            "license_url": VWORLD_LICENSE_URL,
            "aliases": ("국가유산", "문화유산", "문화재"),
        },
        "power_line": {
            "name": "송전선로",
            "provider": "osm",
            "service_type": "overpass",
            "endpoint": "https://overpass-api.de/api/interpreter",
            "data_id": "way[power=line]",
            "geometry_type": "MultiLineString",
            "category": "power",
            "request_crs": "EPSG:4326",
            "response_crs": "EPSG:4326",
            "publisher": OSM_PUBLISHER,
            "dataset_id": "openstreetmap",
            "license_name": OSM_LICENSE,
            "license_url": OSM_LICENSE_URL,
            "aliases": ("송전선", "전력선"),
        },
        "power_support": {
            "name": "송전철탑·지지물",
            "provider": "osm",
            "service_type": "overpass",
            "endpoint": "https://overpass-api.de/api/interpreter",
            "data_id": "node(w.lines)[power=tower|pole]",
            "geometry_type": "MultiPoint",
            "category": "power",
            "request_crs": "EPSG:4326",
            "response_crs": "EPSG:4326",
            "publisher": OSM_PUBLISHER,
            "dataset_id": "openstreetmap",
            "license_name": OSM_LICENSE,
            "license_url": OSM_LICENSE_URL,
            "aliases": ("송전탑", "철탑", "전주"),
        },
        "river_centerline": {
            "name": "하천 중심선",
            "provider": "osm",
            "service_type": "overpass",
            "endpoint": "https://overpass-api.de/api/interpreter",
            "data_id": "way[waterway=river|stream]",
            "geometry_type": "MultiLineString",
            "category": "water",
            "request_crs": "EPSG:4326",
            "response_crs": "EPSG:4326",
            "publisher": OSM_PUBLISHER,
            "dataset_id": "openstreetmap",
            "license_name": OSM_LICENSE,
            "license_url": OSM_LICENSE_URL,
            "aliases": ("하천중심선", "하천망"),
        },
        "river_area": {
            "name": "하천구역·수면",
            "provider": "osm",
            "service_type": "overpass",
            "endpoint": "https://overpass-api.de/api/interpreter",
            "data_id": "natural=water+water=river 또는 waterway=riverbank",
            "geometry_type": "MultiPolygon",
            "category": "water",
            "request_crs": "EPSG:4326",
            "response_crs": "EPSG:4326",
            "publisher": OSM_PUBLISHER,
            "dataset_id": "openstreetmap",
            "license_name": OSM_LICENSE,
            "license_url": OSM_LICENSE_URL,
            "aliases": ("하천구역", "수면"),
        },
    }

    for layer_id, fields in expected.items():
        definition = asdict(catalog.get(layer_id))
        definition.pop("id")
        assert definition == fields


def test_every_packaged_vworld_definition_has_exact_vworld_metadata():
    for definition in LayerCatalog().all():
        if definition.provider != "vworld":
            continue
        assert (
            definition.publisher,
            definition.dataset_id,
            definition.license_name,
            definition.license_url,
        ) == (
            VWORLD_PUBLISHER,
            definition.data_id,
            VWORLD_LICENSE,
            VWORLD_LICENSE_URL,
        )


def _ecology_layer() -> LayerDefinition:
    return LayerDefinition(
        id="ecology_nature",
        name="생태자연도",
        provider="ecology",
        service_type="wfs",
        endpoint="https://apis.data.go.kr/B553084/ecoapi/EcologyzmpService/wfs/getEcologyzmpWFS",
        data_id="tbl_opn_eczm",
        geometry_type="MultiPolygon",
        category="ecology",
        request_crs="EPSG:5186",
        response_crs="EPSG:5186",
        publisher="국립생태원",
        dataset_id="15057288",
        license_name="이용허락범위 제한 없음",
        license_url="https://www.data.go.kr/data/15057288/openapi.do",
        aliases=("생태 자연도", "생태등급"),
    )


def _osm_layer() -> LayerDefinition:
    return LayerDefinition(
        id="power_line",
        name="송전선로",
        provider="osm",
        service_type="overpass",
        endpoint="https://overpass-api.de/api/interpreter",
        data_id="way[power=line]",
        geometry_type="MultiLineString",
        category="power",
        request_crs="EPSG:4326",
        response_crs="EPSG:4326",
        publisher=OSM_PUBLISHER,
        dataset_id="openstreetmap",
        license_name=OSM_LICENSE,
        license_url=OSM_LICENSE_URL,
        aliases=("송전선", "전력선"),
    )


def test_layer_tooltip_lines_lists_provider_routegis_and_provenance_metadata():
    definition = _live_layer("dt_d160", "토지소유공간정보")

    lines = layer_tooltip_lines(definition, None)

    assert lines[:7] == (
        f"공급원: {definition.provider}",
        f"RouteGIS ID: {definition.id}",
        f"요청 데이터 ID: {definition.data_id}",
        f"외부 데이터셋 ID: {definition.dataset_id}",
        f"발행기관: {definition.publisher}",
        f"라이선스: {definition.license_name}",
        f"라이선스 URL: {definition.license_url}",
    )


def test_layer_tooltip_lines_appends_vworld_taxonomy_when_present(
    fixture_directory: Path,
):
    catalog = LayerCatalog(taxonomy_path=fixture_directory / "vworld_taxonomy.yaml")
    definition = catalog.get("heritage_protection")
    taxonomy = catalog.taxonomy_for(definition)

    lines = layer_tooltip_lines(definition, taxonomy)

    assert taxonomy is not None
    assert f"공식 분야: {taxonomy.reference_category}" in lines
    assert f"공식 명칭: {taxonomy.official_name}" in lines
    assert lines.index(f"공식 분야: {taxonomy.reference_category}") > lines.index(
        f"라이선스 URL: {definition.license_url}"
    )


def test_layer_tooltip_lines_omits_vworld_taxonomy_for_non_vworld_definitions():
    ecology = _ecology_layer()
    osm = _osm_layer()

    ecology_lines = layer_tooltip_lines(ecology, None)
    osm_lines = layer_tooltip_lines(osm, None)

    assert not any(line.startswith("공식 분야:") for line in ecology_lines)
    assert not any(line.startswith("공식 명칭:") for line in ecology_lines)
    assert not any(line.startswith("공식 분야:") for line in osm_lines)
    assert not any(line.startswith("공식 명칭:") for line in osm_lines)


def test_layer_tooltip_lines_rejects_taxonomy_for_non_vworld_definition():
    ecology = _ecology_layer()
    taxonomy = TaxonomyEntry(
        official_name="잘못 연결된 공식 명칭",
        reference_category="잘못 연결된 분야",
    )

    lines = layer_tooltip_lines(ecology, taxonomy)

    assert not any(line.startswith("공식 분야:") for line in lines)
    assert not any(line.startswith("공식 명칭:") for line in lines)


def test_layer_tooltip_lines_omits_vworld_taxonomy_for_unclassified_vworld_layers(
    fixture_directory: Path,
):
    catalog = LayerCatalog(taxonomy_path=fixture_directory / "vworld_taxonomy.yaml")
    definition = _live_layer("custom_unclassified", "새 실시간 레이어")

    assert catalog.taxonomy_for(definition) is None
    lines = layer_tooltip_lines(definition, None)

    assert not any(line.startswith("공식 분야:") for line in lines)
    assert not any(line.startswith("공식 명칭:") for line in lines)
