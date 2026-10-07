from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode

from ...config import resource_root
from ...errors import ConfigurationError


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: _UniqueKeyLoader,
    node: MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key: {key}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass(frozen=True)
class TaxonomyEntry:
    official_name: str
    reference_category: str


@dataclass(frozen=True)
class VWorldTaxonomy:
    schema_version: int
    source_url: str
    retrieved_at: str
    source_logical_service_count: int
    expanded_wfs_id_count: int
    categories: Mapping[str, tuple[str, ...]]
    layers: Mapping[str, TaxonomyEntry]

    def get(self, data_id: str) -> TaxonomyEntry | None:
        return self.layers.get(data_id.casefold())

    def aliases_for(self, reference_category: str) -> tuple[str, ...]:
        return self.categories.get(reference_category, ())


def _mapping(value: Any, field: str) -> dict[Any, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"'{field}' 항목은 객체여야 합니다.")
    return value


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"'{field}' 항목은 비어 있지 않은 문자열이어야 합니다.")
    return value.strip()


def _positive_int(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"'{field}' 항목은 양의 정수여야 합니다.")
    return value


def _load_categories(raw: Any) -> Mapping[str, tuple[str, ...]]:
    categories: dict[str, tuple[str, ...]] = {}
    normalized: set[str] = set()
    for raw_name, raw_definition in _mapping(raw, "categories").items():
        name = _text(raw_name, "categories key")
        normalized_name = name.casefold()
        if normalized_name in normalized:
            raise ValueError(f"분야명이 대소문자만 다르게 중복되었습니다: {name}")
        definition = _mapping(raw_definition, f"categories.{name}")
        raw_aliases = definition.get("aliases")
        if not isinstance(raw_aliases, list):
            raise ValueError(f"'categories.{name}.aliases' 항목은 목록이어야 합니다.")
        aliases = tuple(_text(alias, f"categories.{name}.aliases") for alias in raw_aliases)
        if len({alias.casefold() for alias in aliases}) != len(aliases):
            raise ValueError(f"분야 동의어가 중복되었습니다: {name}")
        categories[name] = aliases
        normalized.add(normalized_name)
    if not categories:
        raise ValueError("'categories' 항목이 비어 있습니다.")
    return MappingProxyType(categories)


def _load_layers(
    raw: Any,
    categories: Mapping[str, tuple[str, ...]],
) -> Mapping[str, TaxonomyEntry]:
    layers: dict[str, TaxonomyEntry] = {}
    for raw_data_id, raw_definition in _mapping(raw, "layers").items():
        data_id = _text(raw_data_id, "layers key")
        normalized_id = data_id.casefold()
        if normalized_id in layers:
            raise ValueError(f"WFS ID가 대소문자만 다르게 중복되었습니다: {data_id}")
        definition = _mapping(raw_definition, f"layers.{data_id}")
        official_name = _text(definition.get("name"), f"layers.{data_id}.name")
        reference_category = _text(definition.get("category"), f"layers.{data_id}.category")
        if reference_category not in categories:
            raise ValueError(f"선언되지 않은 분야입니다: {data_id} -> {reference_category}")
        layers[normalized_id] = TaxonomyEntry(
            official_name=official_name,
            reference_category=reference_category,
        )
    if not layers:
        raise ValueError("'layers' 항목이 비어 있습니다.")
    return MappingProxyType(layers)


def load_vworld_taxonomy(path: Path | None = None) -> VWorldTaxonomy:
    taxonomy_path = path or resource_root() / "vworld_wfs_taxonomy.yaml"
    try:
        with taxonomy_path.open("r", encoding="utf-8") as stream:
            raw = yaml.load(stream, Loader=_UniqueKeyLoader)
        data = _mapping(raw, "root")
        schema_version = _positive_int(data.get("schema_version"), "schema_version")
        if schema_version != 1:
            raise ValueError(f"지원하지 않는 schema_version입니다: {schema_version}")
        source_url = _text(data.get("source_url"), "source_url")
        retrieved_at = _text(data.get("retrieved_at"), "retrieved_at")
        date.fromisoformat(retrieved_at)
        source_count = _positive_int(
            data.get("source_logical_service_count"),
            "source_logical_service_count",
        )
        expanded_count = _positive_int(data.get("expanded_wfs_id_count"), "expanded_wfs_id_count")
        categories = _load_categories(data.get("categories"))
        layers = _load_layers(data.get("layers"), categories)
        if expanded_count != len(layers):
            raise ValueError(
                "'expanded_wfs_id_count'가 실제 layers 항목 수와 다릅니다: "
                f"{expanded_count} != {len(layers)}"
            )
        return VWorldTaxonomy(
            schema_version=schema_version,
            source_url=source_url,
            retrieved_at=retrieved_at,
            source_logical_service_count=source_count,
            expanded_wfs_id_count=expanded_count,
            categories=categories,
            layers=layers,
        )
    except (OSError, yaml.YAMLError, TypeError, ValueError) as exc:
        raise ConfigurationError(f"VWorld taxonomy 설정 오류 ({taxonomy_path.name}): {exc}") from exc
