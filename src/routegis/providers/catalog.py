from __future__ import annotations

from pathlib import Path

from ..config import load_layer_catalog
from ..errors import ConfigurationError
from ..models import LayerDefinition
from .vworld.taxonomy import TaxonomyEntry, VWorldTaxonomy, load_vworld_taxonomy


def matches_layer_query(definition: LayerDefinition, query: str) -> bool:
    needle = query.strip().casefold()
    return not needle or any(
        needle in value.casefold()
        for value in (
            definition.provider,
            definition.publisher,
            definition.dataset_id,
            *definition.aliases,
            definition.id,
            definition.name,
            definition.data_id,
        )
    )


class LayerCatalog:
    def __init__(self, path: Path | None = None, *, taxonomy_path: Path | None = None):
        self._layers = load_layer_catalog(path)
        self._taxonomy: VWorldTaxonomy = load_vworld_taxonomy(taxonomy_path)

    def get(self, layer_id: str) -> LayerDefinition:
        try:
            return self._layers[layer_id]
        except KeyError as exc:
            raise ConfigurationError(f"레이어 카탈로그에 없는 공간정보입니다: {layer_id}") from exc

    def select(self, layer_ids: tuple[str, ...]) -> list[LayerDefinition]:
        return [self.get(layer_id) for layer_id in layer_ids]

    def all(self) -> tuple[LayerDefinition, ...]:
        return tuple(self._layers.values())

    def merge_live(self, definitions: list[LayerDefinition]) -> int:
        existing_ids = {definition.id.casefold() for definition in self._layers.values()}
        existing_data_ids = {
            definition.data_id.casefold() for definition in self._layers.values()
        }
        added = 0
        for definition in definitions:
            layer_id = definition.id.casefold()
            data_id = definition.data_id.casefold()
            if (
                definition.provider != "vworld"
                or layer_id in existing_ids
                or data_id in existing_data_ids
            ):
                continue
            self._layers[definition.id] = definition
            existing_ids.add(layer_id)
            existing_data_ids.add(data_id)
            added += 1
        return added

    def snapshot(self) -> dict[str, LayerDefinition]:
        return dict(self._layers)

    def taxonomy_for(self, definition: LayerDefinition) -> TaxonomyEntry | None:
        if definition.provider != "vworld":
            return None
        return self._taxonomy.get(definition.data_id)

    def matches(self, definition: LayerDefinition, query: str) -> bool:
        if matches_layer_query(definition, query):
            return True
        needle = query.strip().casefold()
        if not needle:
            return True
        taxonomy = self.taxonomy_for(definition)
        if taxonomy is None:
            return False
        values = (
            taxonomy.official_name,
            taxonomy.reference_category,
            *self._taxonomy.aliases_for(taxonomy.reference_category),
        )
        return any(needle in value.casefold() for value in values)

    def sort_key(self, definition: LayerDefinition) -> tuple[int, str, str, str, str]:
        taxonomy = self.taxonomy_for(definition)
        return (
            0 if taxonomy is not None else 1,
            taxonomy.reference_category.casefold() if taxonomy is not None else "",
            definition.name.casefold(),
            definition.data_id.casefold(),
            definition.id.casefold(),
        )


def layer_tooltip_lines(
    definition: LayerDefinition,
    taxonomy: TaxonomyEntry | None,
) -> tuple[str, ...]:
    """Compose tooltip lines for a layer list item.

    The tooltip exposes the request ``data_id``, external ``dataset_id``, publisher,
    license name, and license URL for every provider. VWorld taxonomy entries add
    the official reference category and official name when present. Non-VWorld
    providers never receive taxonomy lines.
    """

    base = (
        f"공급원: {definition.provider}",
        f"RouteGIS ID: {definition.id}",
        f"요청 데이터 ID: {definition.data_id}",
        f"외부 데이터셋 ID: {definition.dataset_id}",
        f"발행기관: {definition.publisher}",
        f"라이선스: {definition.license_name}",
        f"라이선스 URL: {definition.license_url}",
    )
    if taxonomy is None or definition.provider != "vworld":
        return base
    return base + (
        f"공식 분야: {taxonomy.reference_category}",
        f"공식 명칭: {taxonomy.official_name}",
    )
