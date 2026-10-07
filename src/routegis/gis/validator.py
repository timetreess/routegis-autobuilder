from __future__ import annotations

from shapely import normalize, to_wkb

from ..models import SpatialLayer, ValidationIssue


def validate_layer(layer: SpatialLayer) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    fingerprints: set[bytes] = set()
    expected = layer.geometry_type.replace("Multi", "")
    for index, feature in enumerate(layer.features, start=1):
        geometry = feature.geometry
        if geometry.is_empty:
            issues.append(ValidationIssue("error", "EMPTY_GEOMETRY", f"{index}번 객체의 Geometry가 비어 있습니다.", layer.name))
            continue
        if not geometry.is_valid:
            issues.append(ValidationIssue("error", "INVALID_GEOMETRY", f"{index}번 객체의 Geometry가 유효하지 않습니다.", layer.name))
        if expected not in geometry.geom_type:
            issues.append(
                ValidationIssue(
                    "error",
                    "GEOMETRY_TYPE",
                    f"{index}번 객체 형식({geometry.geom_type})이 레이어 형식({layer.geometry_type})과 다릅니다.",
                    layer.name,
                )
            )
        fingerprint = to_wkb(normalize(geometry), hex=False)
        if fingerprint in fingerprints:
            issues.append(ValidationIssue("warning", "DUPLICATE_GEOMETRY", f"{index}번 객체가 중복되었습니다.", layer.name))
        fingerprints.add(fingerprint)
    if not layer.features:
        issues.append(ValidationIssue("warning", "EMPTY_LAYER", "조회된 객체가 없습니다.", layer.name))
    return issues


def validate_route_and_aoi(route: SpatialLayer, aoi: SpatialLayer) -> list[ValidationIssue]:
    issues = validate_layer(route) + validate_layer(aoi)
    if not route.features or sum(feature.geometry.length for feature in route.features) <= 0:
        issues.append(ValidationIssue("error", "ROUTE_LENGTH", "노선 길이가 0입니다.", route.name))
    if route.features and aoi.features and not all(
        aoi.features[0].geometry.covers(feature.geometry) for feature in route.features
    ):
        issues.append(ValidationIssue("error", "AOI_COVERAGE", "분석 범위가 노선 전체를 포함하지 않습니다.", aoi.name))
    return issues


def validate_layer_intersects_aoi(layer: SpatialLayer, aoi: SpatialLayer) -> list[ValidationIssue]:
    if not layer.features:
        return []
    aoi_geometry = aoi.features[0].geometry
    if not any(feature.geometry.intersects(aoi_geometry) for feature in layer.features):
        return [
            ValidationIssue(
                "error",
                "CRS_MISMATCH",
                "다운로드 데이터가 사업구간과 전혀 겹치지 않습니다. 좌표계를 확인해 주세요.",
                layer.name,
            )
        ]
    return []
