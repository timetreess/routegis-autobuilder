from __future__ import annotations

from shapely.geometry import LineString, Point, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform, unary_union

from ..errors import CrsError
from ..models import SpatialFeature, SpatialLayer, ValidationIssue


KOREAN_PROJECTED_CRS = (
    ("EPSG:5185", "Korea 2000 / GRS80 서부원점 2010"),
    ("EPSG:5186", "Korea 2000 / GRS80 중부원점 2010"),
    ("EPSG:5187", "Korea 2000 / GRS80 동부원점 2010"),
    ("EPSG:5188", "Korea 2000 / GRS80 동해원점 2010"),
    ("EPSG:5179", "Korea 2000 / GRS80 통합좌표계 (UTM-K)"),
    ("EPSG:5180", "Korea 2000 / GRS80 서부원점"),
    ("EPSG:5181", "Korea 2000 / GRS80 중부원점"),
    ("EPSG:5182", "Korea 2000 / GRS80 중부원점 (제주)"),
    ("EPSG:5183", "Korea 2000 / GRS80 동부원점"),
    ("EPSG:5184", "Korea 2000 / GRS80 동해원점"),
    ("EPSG:5173", "Korean 1985 / Bessel 보정 서부원점"),
    ("EPSG:5174", "Korean 1985 / Bessel 보정 중부원점"),
    ("EPSG:5175", "Korean 1985 / Bessel 보정 중부원점 (제주)"),
    ("EPSG:5176", "Korean 1985 / Bessel 보정 동부원점"),
    ("EPSG:5177", "Korean 1985 / Bessel 보정 동해원점"),
    ("EPSG:5178", "Korean 1985 / Bessel 통합좌표계 (UTM-K)"),
    ("EPSG:2098", "Korean 1985 / Bessel 서부원점"),
    ("EPSG:2097", "Korean 1985 / Bessel 중부원점"),
    ("EPSG:2096", "Korean 1985 / Bessel 동부원점"),
    ("EPSG:5167", "Korean 1985 / Bessel 동해원점"),
    ("EPSG:5168", "Korean 1985 / Bessel 중부원점 (제주)"),
    ("EPSG:5169", "Tokyo 1892 / 구한국 서부원점"),
    ("EPSG:5170", "Tokyo 1892 / 구한국 중부원점"),
    ("EPSG:5171", "Tokyo 1892 / 구한국 동부원점"),
    ("EPSG:5172", "Tokyo 1892 / 구한국 동해원점"),
    ("EPSG:32651", "WGS 84 / UTM 51N"),
    ("EPSG:32652", "WGS 84 / UTM 52N"),
    ("EPSG:3857", "WGS 84 / Web Mercator"),
)


def _pyproj():
    try:
        from pyproj import CRS, Transformer
    except ImportError as exc:
        raise CrsError("좌표변환 기능을 사용하려면 pyproj를 설치해 주세요.") from exc
    return CRS, Transformer


def normalize_crs(crs: str) -> str:
    CRS, _ = _pyproj()
    try:
        parsed = CRS.from_user_input(crs)
    except Exception as exc:
        raise CrsError(f"좌표계를 확인할 수 없습니다: {crs}") from exc
    authority = parsed.to_authority()
    return f"{authority[0]}:{authority[1]}" if authority else parsed.to_string()


def validate_projected_metre_crs(crs: str) -> None:
    CRS, _ = _pyproj()
    try:
        parsed = CRS.from_user_input(crs)
    except Exception as exc:
        raise CrsError(f"좌표계를 확인할 수 없습니다: {crs}") from exc
    if not parsed.is_projected:
        raise CrsError("거리 기반 분석에는 미터 단위의 투영좌표계를 선택해 주세요.")
    axes = parsed.axis_info
    if not axes or any(axis.unit_name.lower() not in {"metre", "meter"} for axis in axes[:2]):
        raise CrsError("선택한 좌표계의 단위가 미터가 아닙니다.")


def transform_geometry(geometry: BaseGeometry, source_crs: str, target_crs: str) -> BaseGeometry:
    source = normalize_crs(source_crs)
    target = normalize_crs(target_crs)
    if source == target:
        return geometry
    _, Transformer = _pyproj()
    try:
        transformer = Transformer.from_crs(source, target, always_xy=True)
        return transform(transformer.transform, geometry)
    except Exception as exc:
        raise CrsError(f"좌표변환에 실패했습니다: {source} → {target}") from exc


def transform_bounds(
    bounds: tuple[float, float, float, float],
    source_crs: str,
    target_crs: str,
    *,
    densify_pts: int = 21,
) -> tuple[float, float, float, float]:
    if densify_pts < 0:
        raise ValueError("densify_pts must be non-negative")

    source = normalize_crs(source_crs)
    target = normalize_crs(target_crs)
    float_bounds = tuple(float(value) for value in bounds)
    if source == target:
        return float_bounds

    _, Transformer = _pyproj()
    try:
        transformed = Transformer.from_crs(source, target, always_xy=True).transform_bounds(
            *float_bounds,
            densify_pts=densify_pts,
        )
        return tuple(float(value) for value in transformed)
    except Exception as exc:
        raise CrsError(f"좌표변환에 실패했습니다: {source} → {target}") from exc


def transform_layer(layer: SpatialLayer, target_crs: str) -> SpatialLayer:
    target = normalize_crs(target_crs)
    if not layer.features:
        return SpatialLayer(
            name=layer.name,
            display_name=layer.display_name,
            crs=target,
            geometry_type=layer.geometry_type,
            category=layer.category,
            source=layer.source,
        )

    source = normalize_crs(layer.crs)
    transformer = None
    if source != target:
        _, Transformer = _pyproj()
        try:
            transformer = Transformer.from_crs(source, target, always_xy=True)
        except Exception as exc:
            raise CrsError(f"좌표변환에 실패했습니다: {source} → {target}") from exc

    def transform_feature_geometry(geometry: BaseGeometry) -> BaseGeometry:
        if transformer is None:
            return geometry
        try:
            return transform(transformer.transform, geometry)
        except Exception as exc:
            raise CrsError(f"좌표변환에 실패했습니다: {source} → {target}") from exc

    return SpatialLayer(
        name=layer.name,
        display_name=layer.display_name,
        crs=target,
        geometry_type=layer.geometry_type,
        category=layer.category,
        source=layer.source,
        features=[
            SpatialFeature(
                geometry=transform_feature_geometry(feature.geometry),
                properties=dict(feature.properties),
                feature_id=feature.feature_id,
            )
            for feature in layer.features
        ],
    )


def korea_sanity_issues(layer: SpatialLayer) -> list[ValidationIssue]:
    envelopes: list[BaseGeometry] = []
    for feature in layer.features:
        if feature.geometry.is_empty:
            continue
        min_x, min_y, max_x, max_y = feature.geometry.bounds
        if min_x == max_x and min_y == max_y:
            envelopes.append(Point(min_x, min_y))
        elif min_x == max_x or min_y == max_y:
            envelopes.append(LineString(((min_x, min_y), (max_x, max_y))))
        else:
            envelopes.append(box(min_x, min_y, max_x, max_y))
    if not envelopes:
        return [ValidationIssue("error", "CRS_EMPTY", "좌표계 확인을 위한 공간 범위가 없습니다.", layer.name)]
    bounds_geometry = unary_union(envelopes)
    wgs84_bounds = transform_geometry(bounds_geometry, layer.crs, "EPSG:4326").bounds
    min_x, min_y, max_x, max_y = wgs84_bounds
    issues: list[ValidationIssue] = []
    if max_x < 124 or min_x > 132 or max_y < 33 or min_y > 39:
        issues.append(
            ValidationIssue(
                "warning",
                "CRS_OUTSIDE_KOREA",
                "노선이 대한민국 예상 범위(경도 124~132, 위도 33~39) 밖에 있습니다. 좌표계를 확인해 주세요.",
                layer.name,
            )
        )
    if not all(map(lambda value: abs(value) < 1_000_000_000, wgs84_bounds)):
        issues.append(ValidationIssue("error", "CRS_INVALID_EXTENT", "변환된 공간 범위가 비정상입니다.", layer.name))
    return issues


def crs_wkt(crs: str, *, version: str = "WKT2_2019") -> str:
    CRS, _ = _pyproj()
    try:
        return CRS.from_user_input(crs).to_wkt(version=version)
    except Exception as exc:
        raise CrsError(f"좌표계 정의를 만들 수 없습니다: {crs}") from exc
