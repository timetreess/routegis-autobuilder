from __future__ import annotations

from shapely.geometry import LineString, Polygon, box
from shapely.ops import unary_union

from ..errors import CrsError
from ..models import SpatialFeature, SpatialLayer
from .crs import validate_projected_metre_crs


def create_route_layer(route: LineString, crs: str, source_layer: str) -> SpatialLayer:
    if route.is_empty or route.length <= 0:
        raise CrsError("노선 길이가 0이므로 분석할 수 없습니다.")
    return SpatialLayer(
        name="route_centerline",
        display_name="노선 중심선",
        crs=crs,
        geometry_type="LineString",
        category="route",
        features=[SpatialFeature(route, {"source_layer": source_layer, "length": route.length}, "route-1")],
        source="cad",
    )


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
    mode: str = "buffer",
    custom_polygon: Polygon | None = None,
) -> SpatialLayer:
    validate_projected_metre_crs(route_layer.crs)
    if distance <= 0:
        raise CrsError("사업구간 주변 범위는 0보다 커야 합니다.")
    if not route_layer.features:
        raise CrsError("사업구간 주변 범위를 만들 선형 객체가 없습니다.")
    if mode == "buffer":
        # Buffer each line before dissolving: buffering the whole noded CAD
        # network can exhaust GEOS memory when thousands of offsets overlap.
        geometry = unary_union([feature.geometry.buffer(distance) for feature in route_layer.features])
    elif mode == "bbox":
        bounds = route_layer.bounds
        if bounds is None:
            raise CrsError("분석 범위를 생성하지 못했습니다.")
        min_x, min_y, max_x, max_y = bounds
        geometry = box(min_x - distance, min_y - distance, max_x + distance, max_y + distance)
    elif mode == "polygon" and custom_polygon is not None:
        geometry = custom_polygon
    else:
        raise CrsError("지원하지 않는 분석 범위 방식입니다.")
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
                {"mode": mode, "distance_m": distance, "area_m2": geometry.area},
                "aoi-1",
            )
        ],
        source="generated",
    )
