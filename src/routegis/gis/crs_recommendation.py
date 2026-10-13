from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from pyproj import CRS, Transformer
from pyproj.exceptions import ProjError

from ..errors import CrsError
from .crs import KOREAN_PROJECTED_CRS


@dataclass(frozen=True)
class BusinessRegion:
    name: str
    aliases: tuple[str, ...]
    bounds: tuple[float, float, float, float]


# Approximate WGS84 envelopes for candidate screening, NOT administrative polygons.
# Overlap is deliberate: a province alone cannot identify a datum or a survey origin.
BUSINESS_REGIONS = (
    BusinessRegion("서울특별시", ("서울", "서울시"), (126.76, 37.42, 127.19, 37.71)),
    BusinessRegion("부산광역시", ("부산", "부산시"), (128.76, 34.87, 129.31, 35.40)),
    BusinessRegion("대구광역시", ("대구", "대구시"), (128.34, 35.60, 128.99, 36.33)),
    BusinessRegion("인천광역시", ("인천", "인천시"), (124.60, 36.90, 126.82, 37.99)),
    BusinessRegion("광주광역시", ("광주", "광주시"), (126.63, 35.04, 127.02, 35.26)),
    BusinessRegion("대전광역시", ("대전", "대전시"), (127.24, 36.18, 127.57, 36.50)),
    BusinessRegion("울산광역시", ("울산", "울산시"), (128.97, 35.32, 129.60, 35.73)),
    BusinessRegion("세종특별자치시", ("세종", "세종시"), (127.15, 36.40, 127.39, 36.75)),
    BusinessRegion("경기도", ("경기",), (126.35, 36.88, 127.86, 38.30)),
    BusinessRegion("강원특별자치도", ("강원", "강원도"), (126.95, 37.00, 129.40, 38.62)),
    BusinessRegion("충청북도", ("충북",), (127.27, 36.00, 128.66, 37.26)),
    BusinessRegion("충청남도", ("충남",), (125.50, 35.97, 127.65, 37.07)),
    BusinessRegion("전북특별자치도", ("전북", "전라북도", "전북도"), (125.90, 35.30, 127.95, 36.20)),
    BusinessRegion("전라남도", ("전남",), (125.00, 33.80, 127.95, 35.50)),
    BusinessRegion("경상북도", ("경북",), (127.79, 35.55, 131.95, 37.55)),
    BusinessRegion("경상남도", ("경남",), (127.57, 34.54, 129.23, 35.91)),
    BusinessRegion("제주특별자치도", ("제주", "제주도"), (126.08, 33.10, 126.98, 33.97)),
)
KOREA_BOUNDS = (124.0, 33.0, 132.0, 38.7)


@dataclass(frozen=True)
class CrsRecommendation:
    code: str
    name: str
    longitude: float
    latitude: float
    reason: str
    score: int


def resolve_business_region(text: str) -> BusinessRegion | None:
    text = text.strip()
    if not text:
        return None
    # Accept a province followed by a district, e.g. "경기도 수원시".
    province = text.split()[0]
    for region in BUSINESS_REGIONS:
        if province in (region.name, *region.aliases):
            return region
    raise CrsError("사업 지역은 시·도 이름으로 입력해 주세요. 예: 경기도, 전라북도, 전북특별자치도")


def _contains(bounds, x: float, y: float, margin: float = 0.0) -> bool:
    return (
        bounds[0] - margin <= x <= bounds[2] + margin
        and bounds[1] - margin <= y <= bounds[3] + margin
    )


def recommend_crs(
    bounds: tuple[float, float, float, float],
    business_region: str = "",
) -> list[CrsRecommendation]:
    """Rank plausible metre-based CRS interpretations; never certify a CRS.

    Test the extent center and corners with CAD X=easting, Y=northing. Region
    coverage and EPSG area of use rank candidates; ties keep preset order, not a
    spurious distance-to-province-center or statistical confidence percentage.
    """
    region = resolve_business_region(business_region)
    min_x, min_y, max_x, max_y = bounds
    if not all(isfinite(value) for value in bounds) or min_x > max_x or min_y > max_y:
        raise CrsError("DXF 좌표 범위가 유효하지 않습니다.")
    if all(-180 <= x <= 180 for x in (min_x, max_x)) and all(
        -90 <= y <= 90 for y in (min_y, max_y)
    ):
        return []
    target_bounds = region.bounds if region else KOREA_BOUNDS
    xs = [(min_x + max_x) / 2, min_x, min_x, max_x, max_x]
    ys = [(min_y + max_y) / 2, min_y, max_y, min_y, max_y]
    recommendations = []
    for code, name in KOREAN_PROJECTED_CRS:
        try:
            crs = CRS.from_user_input(code)
            longitudes, latitudes = Transformer.from_crs(
                crs, "EPSG:4326", always_xy=True,
            ).transform(xs, ys, errcheck=True)
        except ProjError:
            continue
        if not all(isfinite(value) for value in (*longitudes, *latitudes)):
            continue
        longitude, latitude = longitudes[0], latitudes[0]
        if not _contains(KOREA_BOUNDS, longitude, latitude):
            continue
        # A small margin retains projects straddling a province boundary.
        if not _contains(target_bounds, longitude, latitude, margin=0.1):
            continue
        inside = _contains(target_bounds, longitude, latitude)
        coverage = sum(_contains(target_bounds, x, y) for x, y in zip(longitudes, latitudes))
        in_area = crs.area_of_use is not None and _contains(crs.area_of_use.bounds, longitude, latitude)
        reason = f"{region.name if region else '대한민국'} 대략 범위 {'내' if inside else '인접'}"
        reason += " · EPSG 사용범위 " + ("내" if in_area else "밖")
        recommendations.append(CrsRecommendation(
            code, name, longitude, latitude, reason,
            score=int(inside) * 100 + coverage * 10 + int(in_area),
        ))
    return sorted(recommendations, key=lambda item: -item.score)
