from __future__ import annotations

from shapely.geometry import LineString
from shapely.ops import unary_union

from ..errors import CrsError
from ..models import SpatialFeature, SpatialLayer
from .crs import validate_projected_metre_crs


def create_linework_layer(
    lines_by_layer: dict[str, list[LineString]],
    crs: str,
) -> SpatialLayer:
    features = [
        SpatialFeature(
            line,
            {
                "dxf_layer": layer_name,
                "entity_no": entity_index,
                "length_m": line.length,
            },
            f"{layer_index}-{entity_index}",
        )
        for layer_index, layer_name in enumerate(sorted(lines_by_layer, key=str.casefold), start=1)
        for entity_index, line in enumerate(lines_by_layer[layer_name], start=1)
    ]
    if not features or sum(feature.geometry.length for feature in features) <= 0:
        raise CrsError("선형 객체 길이가 0이므로 분석할 수 없습니다.")
    return SpatialLayer(
        name="linework",
        display_name="선형",
        crs=crs,
        geometry_type="LineString",
        category="route",
        features=features,
        source="cad",
    )


def create_aoi_layer(
    route_layer: SpatialLayer,
    distance: float,
) -> SpatialLayer:
    validate_projected_metre_crs(route_layer.crs)
    if distance <= 0:
        raise CrsError("사업구간 주변 범위는 0보다 커야 합니다.")
    if not route_layer.features:
        raise CrsError("사업구간 주변 범위를 만들 선형 객체가 없습니다.")
    # Buffer each line before dissolving: buffering the whole noded CAD
    # network can exhaust GEOS memory when thousands of offsets overlap.
    geometry = unary_union([feature.geometry.buffer(distance) for feature in route_layer.features])
    if geometry.is_empty or not geometry.is_valid or geometry.area <= 0:
        raise CrsError("분석 범위를 생성하지 못했습니다.")
    return SpatialLayer(
        name="route_buffer",
        display_name="사업구간 주변 범위",
        crs=route_layer.crs,
        geometry_type="Polygon",
        category="route",
        features=[
            SpatialFeature(
                geometry,
                {"mode": "buffer", "distance_m": distance, "area_m2": geometry.area},
                "aoi-1",
            )
        ],
        source="generated",
    )
