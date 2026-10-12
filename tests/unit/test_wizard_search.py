"""Regression tests for the Wizard spatial-data UI, catalog search, and default-state behavior.

Pure helpers are exercised directly. Wizard UI behavior that depends on PyQt is
verified through source-contract tests on ``plugin/wizard.py`` because the regular
test environment cannot import :mod:`qgis.PyQt`.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator
from dataclasses import asdict, replace
from pathlib import Path
from typing import cast

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
    "river_centerline",
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


def test_basic_route_contains_exactly_the_seven_approved_layers_in_order():
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


# ----------------------------------------------------------------------------
# Task 9: tooltip helper, credential behavior, and source-contract checks
# ----------------------------------------------------------------------------


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


# -- Source-contract checks for plugin/wizard.py ---------------------------------


def _wizard_path() -> Path:
    return Path(__file__).resolve().parents[2] / "plugin" / "wizard.py"


def _wizard_source() -> str:
    return _wizard_path().read_text(encoding="utf-8")


def _wizard_tree() -> ast.Module:
    return ast.parse(_wizard_source())


def _find_function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for member in node.body:
                if isinstance(member, ast.FunctionDef) and member.name == name:
                    return member
    raise AssertionError(f"function {name!r} not found at module/class top level")


def _self_attribute(node: ast.AST, attr: str) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
        and node.attr == attr
    )


def _assigned_in(body: Iterable[ast.stmt], attr: str) -> bool:
    for stmt in body:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if _self_attribute(target, attr):
                        return True
    return False


def _is_self_receiver(node: ast.AST) -> bool:
    if isinstance(node, ast.Name) and node.id == "self":
        return True
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    ):
        return True
    return False


def _calls_on_self(body: Iterable[ast.stmt], attr: str) -> Iterator[ast.Call]:
    for stmt in body:
        for node in ast.walk(stmt):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == attr
                and _is_self_receiver(node.func.value)
            ):
                yield node


def _find_call_with_keyword(
    body: Iterable[ast.stmt],
    *,
    method_name: str,
    keyword: str,
) -> ast.Call | None:
    """Find a Call node ``self.<method_name>(..., keyword=..., ...)``."""
    for stmt in body:
        for node in ast.walk(stmt):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != method_name:
                continue
            if not _is_self_receiver(node.func.value):
                continue
            for kw in node.keywords:
                if kw.arg == keyword:
                    return node
    return None


def _enclosing_finally(node: ast.AST, root: ast.AST) -> ast.Try | None:
    """Walk upward through the AST to find the innermost enclosing ``finally`` block.

    The traversal re-parses ancestry by walking child nodes because the parsed
    tree does not keep parent pointers; we use parent reconstruction only when
    needed (and this helper falls back to ``None`` if no ``finally`` encloses).
    """

    parents: dict[int, ast.AST] = {}
    stack: list[tuple[ast.AST, ast.AST | None]] = [(root, None)]
    while stack:
        current, parent = stack.pop()
        if parent is not None:
            parents[id(current)] = parent
        for child in ast.iter_child_nodes(current):
            stack.append((child, current))
    seen: set[int] = set()
    current = node
    while id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ast.Try) and current.finalbody:
            return current
        parent = parents.get(id(current))
        if parent is None:
            return None
        current = parent
    return None


def test_wizard_declares_vworld_and_ecology_password_fields():
    tree = _wizard_tree()
    project_page = _find_function(tree, "_project_page")

    def has_password_echo(body: Iterable[ast.stmt], field: str) -> bool:
        if not _assigned_in(body, field):
            return False
        for stmt in body:
            calls = (node for node in ast.walk(stmt) if isinstance(node, ast.Call))
            for call in calls:
                if not (
                    isinstance(call.func, ast.Attribute)
                    and call.func.attr == "setEchoMode"
                    and _self_attribute(call.func.value, field)
                ):
                    continue
                if len(call.args) != 1:
                    continue
                arg = call.args[0]
                if (
                    isinstance(arg, ast.Attribute)
                    and isinstance(arg.value, ast.Name)
                    and arg.value.id == "QLineEdit"
                    and arg.attr == "Password"
                ):
                    return True
        return False

    assert has_password_echo(project_page.body, "vworld_api_key"), (
        "vworld_api_key must be declared in _project_page with QLineEdit.Password"
    )
    assert has_password_echo(project_page.body, "data_go_kr_service_key"), (
        "data_go_kr_service_key must be declared in _project_page with QLineEdit.Password"
    )


def test_wizard_project_page_labels_both_persisted_credentials():
    project_page = _find_function(_wizard_tree(), "_project_page")
    text = "\n".join(
        cast(str, node.value)
        for stmt in project_page.body
        for node in ast.walk(stmt)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )

    assert "VWorld API Key 입력" in text
    assert "공공데이터포털 서비스키 입력" in text
    assert "임시 입력" not in text


def test_wizard_does_not_offer_a_vworld_domain_field():
    tree = _wizard_tree()
    project_page = ast.unparse(_find_function(tree, "_project_page"))

    assert "self.domain" not in ast.unparse(tree)
    assert "VWorld 등록 도메인" not in project_page


def test_wizard_renders_tooltip_via_layer_tooltip_lines_helper():
    tree = _wizard_tree()
    class_node = next(
        node for node in tree.body if isinstance(node, ast.ClassDef)
    )

    helper_imported = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "routegis.providers.catalog"
        and any(alias.name == "layer_tooltip_lines" for alias in node.names)
        for node in tree.body
    )
    assert helper_imported, (
        "wizard.py must import layer_tooltip_lines from routegis.providers.catalog"
    )

    method_node = next(
        member for member in class_node.body if getattr(member, "name", "") == "_render_layer_list"
    )
    source = ast.unparse(method_node)
    assert "layer_tooltip_lines" in source


def test_wizard_live_refresh_uses_task_8_provider_signature_with_explicit_name():
    tree = _wizard_tree()
    worker = _find_function(tree, "_discover_vworld_catalog")
    refresh = _find_function(tree, "_load_vworld_catalog")
    provider_call = next(
        (
            node
            for node in ast.walk(worker)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_provider"
        ),
        None,
    )
    assert provider_call is not None, (
        "_discover_vworld_catalog must invoke service._provider with provider_name=..."
    )
    provider_name_kw = next(
        kw.value for kw in provider_call.keywords if kw.arg == "provider_name"
    )
    assert isinstance(provider_name_kw, ast.Constant) and provider_name_kw.value == "vworld", (
        "_load_vworld_catalog must pass provider_name=\"vworld\" to honor the Task 8 boundary"
    )

    keyword_values = {keyword.arg: keyword.value for keyword in provider_call.keywords}
    ecology_key = keyword_values.get("data_go_kr_service_key")
    assert isinstance(ecology_key, ast.Constant) and ecology_key.value == ""
    domain = keyword_values.get("domain")
    assert isinstance(domain, ast.Constant) and domain.value is None

    refresh_source = ast.unparse(refresh)
    assert "QgsTask.fromFunction" in refresh_source
    assert "QgsApplication.taskManager().addTask" in refresh_source
    task_call = next(
        node
        for node in ast.walk(refresh)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "fromFunction"
    )
    assert all(keyword.arg != "domain" for keyword in task_call.keywords)
    source = ast.unparse(refresh)
    assert "load_data_go_kr_service_key" not in source
    assert "self.data_go_kr_service_key" not in source

    close_in_finally = any(
        isinstance(node, ast.Try)
        and any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr == "close"
            and isinstance(child.func.value, ast.Name)
            and child.func.value.id == "client"
            for statement in node.finalbody
            for child in ast.walk(statement)
        )
        for node in ast.walk(worker)
    )
    assert close_in_finally


def test_wizard_build_resolves_both_separate_keys_with_correct_precedence():
    tree = _wizard_tree()
    build = _find_function(tree, "_build")

    def assigned_value(name: str) -> ast.AST:
        for node in ast.walk(build):
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == name
                and isinstance(node.value, ast.BoolOp)
            ):
                return node.value
        raise AssertionError(f"{name} credential resolution assignment not found")

    def assert_precedence(value: ast.AST, loader: str, field: str) -> None:
        assert isinstance(value, ast.BoolOp) and isinstance(value.op, ast.Or)
        assert len(value.values) == 2
        temporary_call, load_call = value.values
        assert (
            isinstance(load_call, ast.Call)
            and isinstance(load_call.func, ast.Name)
            and load_call.func.id == loader
            and len(load_call.args) == 1
            and isinstance(load_call.args[0], ast.Name)
            and load_call.args[0].id == "PLUGIN_DOTENV"
        )
        assert (
            isinstance(temporary_call, ast.Call)
            and isinstance(temporary_call.func, ast.Attribute)
            and temporary_call.func.attr == "strip"
            and isinstance(temporary_call.func.value, ast.Call)
            and isinstance(temporary_call.func.value.func, ast.Attribute)
            and temporary_call.func.value.func.attr == "text"
            and _self_attribute(temporary_call.func.value.func.value, field)
        )

    assert_precedence(assigned_value("api_key"), "load_vworld_api_key", "vworld_api_key")
    assert_precedence(
        assigned_value("data_go_kr_service_key"),
        "load_data_go_kr_service_key",
        "data_go_kr_service_key",
    )

    service_build = next(
        node
        for node in ast.walk(build)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "build"
        and isinstance(node.func.value, ast.Attribute)
        and _self_attribute(node.func.value, "service")
    )
    build_keywords = {keyword.arg: keyword.value for keyword in service_build.keywords}
    assert isinstance(build_keywords["api_key"], ast.Name)
    assert build_keywords["api_key"].id == "api_key"
    assert isinstance(build_keywords["data_go_kr_service_key"], ast.Name)
    assert build_keywords["data_go_kr_service_key"].id == "data_go_kr_service_key"


def test_wizard_loads_and_persists_both_key_fields():
    tree = _wizard_tree()
    project_page = _find_function(tree, "_project_page")
    save_credentials = _find_function(tree, "_save_credentials")
    build = _find_function(tree, "_build")

    loaded_keys = {
        call.args[0].value
        for call in ast.walk(project_page)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "value"
        and _self_attribute(call.func.value, "recent")
        and call.args
        and isinstance(call.args[0], ast.Constant)
    }
    assert {"vworld_api_key", "data_go_kr_service_key"} <= loaded_keys

    save_source = ast.unparse(save_credentials)
    assert "vworld_api_key" in save_source
    assert "data_go_kr_service_key" in save_source
    assert "self.recent.setValue" in save_source
    assert "self.recent.remove" in save_source
    assert "self.recent.sync" in save_source
    assert "self._save_credentials()" in ast.unparse(build)

    cleared_fields = {
        node.func.value.attr
        for node in ast.walk(build)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "clear"
        and isinstance(node.func.value, ast.Attribute)
        and isinstance(node.func.value.value, ast.Name)
        and node.func.value.value.id == "self"
    }
    assert not {"vworld_api_key", "data_go_kr_service_key"} & cleared_fields


def test_wizard_always_uses_all_supported_dxf_objects_for_aoi():
    tree = _wizard_tree()
    analyze = ast.unparse(_find_function(tree, "_analyze"))
    build = ast.unparse(_find_function(tree, "_build"))
    validate = ast.unparse(_find_function(tree, "validateCurrentPage"))

    assert "QMessageBox.question" not in analyze
    assert "self.project_type = 'non_linear'" in analyze
    assert "create_preview_shapefiles" not in analyze
    assert "add_preview_layers" not in analyze
    assert "source_layers=()" in build
    assert "project_type='non_linear'" in build
    assert "_selected_route_layers" not in build
    assert "_selected_route_layers" not in validate


def test_wizard_preserves_checked_ids_before_refreshing_layer_list():
    tree = _wizard_tree()
    refresh = _find_function(tree, "_load_vworld_catalog")
    finished = _find_function(tree, "_finish_vworld_catalog")

    captures_checked_ids = any(
        isinstance(node, ast.Assign)
        and any(_self_attribute(target, "_vworld_checked_ids") for target in node.targets)
        and any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr == "_selected_layers"
            for child in ast.walk(node.value)
        )
        for node in ast.walk(refresh)
    )
    assert captures_checked_ids

    render_call = next(
        node
        for node in ast.walk(finished)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_render_layer_list"
    )
    assert len(render_call.args) == 1
    assert _self_attribute(render_call.args[0], "_vworld_checked_ids")


def test_wizard_redacts_errors_at_final_ui_sinks():
    tree = _wizard_tree()
    worker = ast.unparse(_find_function(tree, "_discover_vworld_catalog"))
    refresh = ast.unparse(_find_function(tree, "_load_vworld_catalog"))
    build = ast.unparse(_find_function(tree, "_build"))

    assert "redact_sensitive_text" in worker
    assert "redact_sensitive_text" in refresh
    assert "redact_sensitive_text" in build


def test_wizard_invalidates_analysis_when_drawing_changes():
    tree = _wizard_tree()
    project_page = ast.unparse(_find_function(tree, "_project_page"))
    invalidate = ast.unparse(_find_function(tree, "_invalidate_analysis"))

    assert "self.drawing.textChanged.connect(self._invalidate_analysis)" in project_page
    assert "self.project_type = None" in invalidate
    assert "self.candidate_table.setRowCount(0)" in invalidate


def test_wizard_rejects_blank_output_directory_before_build():
    output_directory = ast.unparse(_find_function(_wizard_tree(), "_output_directory"))

    assert "self.output_directory.text().strip()" in output_directory
    assert "if not output_directory:" in output_directory
    assert "raise RouteGISError" in output_directory


def test_wizard_opens_project_through_qgis_interface():
    open_project = ast.unparse(_find_function(_wizard_tree(), "_open_project"))

    assert "self.iface.addProject" in open_project
    assert "QgsProject.instance().read" not in open_project
