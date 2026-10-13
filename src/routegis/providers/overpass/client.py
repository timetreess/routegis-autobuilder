from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from urllib.parse import urlsplit

import httpx
from shapely import make_valid, normalize
from shapely.errors import ShapelyError
from shapely.geometry import LineString, MultiLineString, MultiPoint, MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from ... import __version__
from ...errors import CrsError, ProviderError
from ...gis import AoiTilePlanner, compute_request_budget
from ...gis.crs import normalize_crs, transform_layer
from ...models import DownloadResult, LayerDefinition, SpatialFeature, SpatialLayer
from ..download import DEFAULT_MAX_RESPONSE_BYTES, FetchContext, read_response_bytes


DEFAULT_OVERPASS_ENDPOINT = "https://overpass-api.de/api/interpreter"
FALLBACK_OVERPASS_ENDPOINTS = (
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
)
OVERPASS_USER_AGENT = f"RouteGIS-AutoBuilder/{__version__}"
TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})
MAX_RETRIES = 2
MAX_RING_SEARCH_STATES = 50_000
MAX_RING_SEARCH_BRANCHES = 100_000
MAX_SOURCE_CANONICAL_DEPTH = 128
MAX_SOURCE_CANONICAL_ITEMS = 1_000_000

_POWER_LAYER_IDS = frozenset({"power_line", "power_support"})
_RIVER_LAYER_IDS = frozenset({"river_centerline", "river_area"})
_GEOMETRY_TYPES = {
    "power_line": "MultiLineString",
    "power_support": "MultiPoint",
    "river_centerline": "MultiLineString",
    "river_area": "MultiPolygon",
}

_Coordinate = tuple[float, float]
_Ring = tuple[_Coordinate, ...]
_RingSegment = tuple[str, _Ring]
_RingSearchState = tuple[_Ring, tuple[_RingSegment, ...], tuple[_Ring, ...]]


@dataclass(frozen=True)
class _Candidate:
    identity: str
    feature: SpatialFeature
    member_way_ids: frozenset[str] = frozenset()


class OverpassClient:
    def __init__(
        self,
        *,
        endpoint: str = DEFAULT_OVERPASS_ENDPOINT,
        timeout: float = 180.0,
        retries: int = MAX_RETRIES,
        backoff: float = 0.5,
        tile_size_m: float = 25_000,
        request_budget_minimum: int = 100,
        request_budget_per_tile: int = 16,
        request_budget_maximum: int = 5_000,
        max_split_depth: int = 32,
        minimum_tile_size_m: float = 50,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._endpoint = self._validated_endpoint(endpoint)
        # Custom endpoints stay pinned; public-server failover shares the retry budget.
        self._request_endpoints = (
            (self._endpoint, *FALLBACK_OVERPASS_ENDPOINTS)
            if self._endpoint == DEFAULT_OVERPASS_ENDPOINT
            else (self._endpoint,)
        )
        for name, value in (
            ("timeout", timeout),
            ("tile_size_m", tile_size_m),
            ("minimum_tile_size_m", minimum_tile_size_m),
        ):
            if not isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if request_budget_minimum < 0:
            raise ValueError("request_budget_minimum must be non-negative")
        if request_budget_per_tile < 0:
            raise ValueError("request_budget_per_tile must be non-negative")
        if request_budget_maximum <= 0:
            raise ValueError("request_budget_maximum must be positive")
        if max_split_depth < 0:
            raise ValueError("max_split_depth must be non-negative")
        if not 1 <= max_response_bytes <= DEFAULT_MAX_RESPONSE_BYTES:
            raise ValueError(
                "max_response_bytes must be between 1 and "
                f"{DEFAULT_MAX_RESPONSE_BYTES}"
            )

        self._retries = min(max(int(retries), 0), MAX_RETRIES)
        self._backoff = max(float(backoff), 0.0)
        self._tile_size_m = float(tile_size_m)
        self._request_budget_minimum = int(request_budget_minimum)
        self._request_budget_per_tile = int(request_budget_per_tile)
        self._request_budget_maximum = int(request_budget_maximum)
        self._max_split_depth = int(max_split_depth)
        self._minimum_tile_size_m = float(minimum_tile_size_m)
        self._max_response_bytes = int(max_response_bytes)
        self._sleep = sleep
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
        )

    @property
    def endpoint(self) -> str:
        return self._endpoint

    @staticmethod
    def _validated_endpoint(endpoint: str) -> str:
        if not isinstance(endpoint, str) or not endpoint or endpoint != endpoint.strip():
            raise ValueError("endpoint must be an absolute HTTPS URL")
        if any(character.isspace() or ord(character) < 32 for character in endpoint):
            raise ValueError("endpoint must be an absolute HTTPS URL")
        try:
            parsed = urlsplit(endpoint)
            hostname = parsed.hostname
            _ = parsed.port
        except ValueError as exc:
            raise ValueError("endpoint must be an absolute HTTPS URL") from exc
        if (
            parsed.scheme.casefold() != "https"
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("endpoint must be an absolute HTTPS URL without credentials or query data")
        return endpoint

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> OverpassClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @staticmethod
    def _coordinate(value: float) -> str:
        return format(value, ".15g")

    @classmethod
    def _query(
        cls,
        definition: LayerDefinition,
        bounds: tuple[float, float, float, float],
    ) -> str:
        west, south, east, north = bounds
        bbox = ",".join(
            cls._coordinate(value) for value in (south, west, north, east)
        )
        if definition.id in _POWER_LAYER_IDS:
            return (
                "[out:json][timeout:180];\n"
                f'way["power"="line"]({bbox})->.lines;\n'
                "(\n"
                "  .lines;\n"
                '  node(w.lines)["power"~"^(tower|pole)$"];\n'
                ");\n"
                "out body geom;"
            )
        if definition.id in _RIVER_LAYER_IDS:
            return (
                "[out:json][timeout:180];\n"
                "(\n"
                f'  way["waterway"~"^(river|stream)$"]({bbox});\n'
                f'  way["natural"="water"]["water"="river"]({bbox});\n'
                f'  relation["natural"="water"]["water"="river"]({bbox});\n'
                f'  way["waterway"="riverbank"]({bbox});\n'
                f'  relation["waterway"="riverbank"]({bbox});\n'
                ");\n"
                "out body geom;"
            )
        raise ProviderError(f"지원하지 않는 Overpass 레이어입니다: {definition.id}")

    def _response_bytes(self, query: str, context: FetchContext) -> bytes:
        last_transport_error: httpx.HTTPError | None = None
        for attempt in range(self._retries + 1):
            context.before_request()
            endpoint_index = min(attempt, len(self._request_endpoints) - 1)
            endpoint = self._request_endpoints[endpoint_index]
            retry_transient_status = False
            try:
                with self._client.stream(
                    "POST",
                    endpoint,
                    content=query.encode("utf-8"),
                    headers={
                        "Content-Type": "text/plain; charset=utf-8",
                        "User-Agent": OVERPASS_USER_AGENT,
                    },
                    follow_redirects=False,
                ) as response:
                    status = response.status_code
                    if status in TRANSIENT_STATUS and attempt < self._retries:
                        retry_transient_status = True
                    elif 300 <= status < 400:
                        raise ProviderError(
                            f"Overpass 리디렉션 응답을 거부했습니다: HTTP {status}"
                        )
                    elif status >= 400:
                        message = f"Overpass 요청에 실패했습니다: HTTP {status}"
                        if status in TRANSIENT_STATUS:
                            message += (
                                " · 서버 혼잡, 일시적 장애 또는 응답 시간 초과. "
                                f"마지막 서버={urlsplit(endpoint).hostname}, "
                                f"전송 시도={attempt + 1}회. 잠시 후 다시 시도해 주세요."
                            )
                        raise ProviderError(message)
                    else:
                        content = read_response_bytes(response, self._max_response_bytes, "Overpass")
                        # Reuse the working server for subsequent tiles and layers.
                        self._request_endpoints = (
                            self._request_endpoints[endpoint_index:]
                            + self._request_endpoints[:endpoint_index]
                        )
                        return content
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_transport_error = exc
                if attempt < self._retries:
                    self._sleep(self._backoff * (2**attempt))
                    continue
                break
            except httpx.HTTPError as exc:
                last_transport_error = exc
                break
            if retry_transient_status:
                self._sleep(self._backoff * (2**attempt))
                continue

        if last_transport_error is None:
            raise ProviderError("Overpass 요청에 실패했습니다.")
        # Explain common PC-specific failures without echoing proxy credentials.
        detail = str(last_transport_error).casefold()
        if "certificate_verify_failed" in detail or "certificate verify failed" in detail:
            hint = "HTTPS 인증서 검증 실패. PC의 신뢰 인증서와 사내 보안 프록시 설정을 확인해 주세요."
        elif "getaddrinfo" in detail or "name or service not known" in detail:
            hint = "DNS 조회 실패. 인터넷 연결과 DNS 설정을 확인해 주세요."
        elif isinstance(last_transport_error, httpx.TimeoutException):
            hint = "서버 연결 또는 응답 시간 초과. 네트워크 상태를 확인한 뒤 다시 시도해 주세요."
        elif isinstance(last_transport_error, httpx.ProxyError):
            hint = "프록시 연결 실패. 프록시 주소와 인증 설정을 확인해 주세요."
        else:
            hint = "서버 통신 실패. 인터넷 연결, 프록시와 방화벽 설정을 확인해 주세요."
        raise ProviderError(
            f"Overpass 요청에 실패했습니다: {type(last_transport_error).__name__} · {hint}"
        ) from last_transport_error

    @staticmethod
    def _payload(content: bytes) -> dict[str, object]:
        try:
            payload = json.loads(content)
        except RecursionError as exc:
            raise ProviderError("Overpass JSON 중첩 depth 한도를 초과했습니다.") from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ProviderError("Overpass JSON 응답을 읽을 수 없습니다.") from exc
        if not isinstance(payload, dict):
            raise ProviderError("Overpass 응답이 JSON 객체가 아닙니다.")
        remark = payload.get("remark")
        if isinstance(remark, str) and remark.strip():
            raise ProviderError(f"Overpass 서비스 오류: {remark.strip()}")
        if not isinstance(payload.get("elements"), list):
            raise ProviderError("Overpass 응답의 elements 형식이 올바르지 않습니다.")
        return payload

    @staticmethod
    def _element_identity(element: Mapping[str, object], expected_type: str) -> str:
        element_type = element.get("type")
        if element_type != expected_type:
            raise ProviderError(
                f"Overpass 객체 유형이 올바르지 않습니다: 예상 {expected_type}"
            )
        identifier = element.get("id")
        if isinstance(identifier, bool) or not isinstance(identifier, int) or identifier <= 0:
            raise ProviderError("Overpass 객체 ID는 양의 정수여야 합니다.")
        return f"{expected_type}/{identifier}"

    @staticmethod
    def _tags(element: Mapping[str, object]) -> dict[str, str]:
        raw_tags = element.get("tags", {})
        if not isinstance(raw_tags, Mapping):
            raise ProviderError("Overpass 객체 tags 형식이 올바르지 않습니다.")
        tags: dict[str, str] = {}
        for key, value in raw_tags.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise ProviderError("Overpass 객체 tags는 문자열이어야 합니다.")
            tags[key] = value
        return tags

    @staticmethod
    def _ordinate(value: object, name: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ProviderError(f"Overpass {name} 좌표가 숫자가 아닙니다.")
        ordinate = float(value)
        if not isfinite(ordinate):
            raise ProviderError(f"Overpass {name} 좌표는 유한해야 합니다.")
        return ordinate

    @classmethod
    def _position(cls, value: object) -> tuple[float, float]:
        if not isinstance(value, Mapping):
            raise ProviderError("Overpass Geometry 좌표 형식이 올바르지 않습니다.")
        latitude = cls._ordinate(value.get("lat"), "위도")
        longitude = cls._ordinate(value.get("lon"), "경도")
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise ProviderError("Overpass 위도 또는 경도 범위를 벗어났습니다.")
        return longitude, latitude

    @classmethod
    def _way_coordinates(
        cls,
        raw_geometry: object,
        identity: str,
    ) -> list[tuple[float, float]]:
        if not isinstance(raw_geometry, list):
            raise ProviderError(f"Overpass way Geometry가 없습니다: {identity}")
        coordinates: list[tuple[float, float]] = []
        for item in raw_geometry:
            position = cls._position(item)
            if not coordinates or position != coordinates[-1]:
                coordinates.append(position)
        if len(coordinates) < 2:
            raise ProviderError(
                f"Overpass way Geometry에는 서로 다른 좌표가 두 개 이상 필요합니다: {identity}"
            )
        return coordinates

    @staticmethod
    def _validated_ring(
        coordinates: Sequence[tuple[float, float]],
        name: str,
        *,
        close: bool,
    ) -> list[tuple[float, float]]:
        ring = list(coordinates)
        if close and ring[0] != ring[-1]:
            ring.append(ring[0])
        if ring[0] != ring[-1]:
            raise ProviderError(f"Overpass {name} 링을 닫을 수 없습니다.")
        distinct = set(ring[:-1])
        if len(distinct) < 3:
            raise ProviderError(
                f"Overpass {name} 링에는 서로 다른 좌표가 세 개 이상 필요합니다."
            )
        try:
            hull = MultiPoint(list(distinct)).convex_hull
            min_x, min_y, max_x, max_y = hull.bounds
            envelope_area = (max_x - min_x) * (max_y - min_y)
            if (
                hull.geom_type != "Polygon"
                or envelope_area <= 0
                or hull.area <= envelope_area * 1e-12
            ):
                raise ProviderError(f"Overpass {name} 링이 퇴화했습니다.")
        except ShapelyError as exc:
            raise ProviderError(f"Overpass {name} 링을 만들 수 없습니다.") from exc
        return ring

    @classmethod
    def _normalize_multi_polygon(cls, geometry: BaseGeometry, name: str) -> MultiPolygon:
        try:
            repaired = geometry if geometry.is_valid else make_valid(geometry)
        except (ShapelyError, TypeError, ValueError) as exc:
            raise ProviderError(f"Overpass {name} Polygon을 복구할 수 없습니다.") from exc

        polygons: list[Polygon] = []
        incompatible: list[str] = []

        def collect(candidate: BaseGeometry) -> None:
            if candidate.is_empty:
                return
            if candidate.geom_type == "Polygon":
                polygons.append(candidate)
            elif candidate.geom_type in {"MultiPolygon", "GeometryCollection"}:
                for child in candidate.geoms:
                    collect(child)
            else:
                incompatible.append(candidate.geom_type)

        collect(repaired)
        if incompatible or not polygons:
            types = ", ".join(sorted(set(incompatible)))
            suffix = f": 호환되지 않는 부분 {types}" if types else ""
            raise ProviderError(f"Overpass {name} Polygon을 복구할 수 없습니다{suffix}.")
        try:
            normalized = MultiPolygon(polygons)
        except (ShapelyError, TypeError, ValueError) as exc:
            raise ProviderError(f"Overpass {name} Polygon을 복구할 수 없습니다.") from exc
        if normalized.is_empty or not normalized.is_valid:
            raise ProviderError(f"Overpass {name} Polygon을 복구할 수 없습니다.")
        return normalized

    @classmethod
    def _line_candidate(cls, element: Mapping[str, object]) -> _Candidate:
        identity = cls._element_identity(element, "way")
        coordinates = cls._way_coordinates(element.get("geometry"), identity)
        try:
            line = LineString(coordinates)
            geometry = MultiLineString([line])
        except (ShapelyError, TypeError, ValueError) as exc:
            raise ProviderError(f"Overpass LineString을 만들 수 없습니다: {identity}") from exc
        if line.is_empty or line.length <= 0 or not line.is_valid:
            raise ProviderError(f"Overpass LineString이 올바르지 않습니다: {identity}")
        return _Candidate(
            identity=identity,
            feature=SpatialFeature(
                geometry=geometry,
                properties=cls._tags(element),
                feature_id=identity,
            ),
        )

    @classmethod
    def _point_candidate(cls, element: Mapping[str, object]) -> _Candidate:
        identity = cls._element_identity(element, "node")
        longitude, latitude = cls._position(element)
        try:
            point = Point(longitude, latitude)
            geometry = MultiPoint([point])
        except (ShapelyError, TypeError, ValueError) as exc:
            raise ProviderError(f"Overpass Point를 만들 수 없습니다: {identity}") from exc
        return _Candidate(
            identity=identity,
            feature=SpatialFeature(
                geometry=geometry,
                properties=cls._tags(element),
                feature_id=identity,
            ),
        )

    @classmethod
    def _way_polygon_candidate(cls, element: Mapping[str, object]) -> _Candidate:
        identity = cls._element_identity(element, "way")
        coordinates = cls._way_coordinates(element.get("geometry"), identity)
        ring = cls._validated_ring(coordinates, identity, close=True)
        try:
            polygon = Polygon(ring)
        except (ShapelyError, TypeError, ValueError) as exc:
            raise ProviderError(f"Overpass Polygon을 만들 수 없습니다: {identity}") from exc
        geometry = cls._normalize_multi_polygon(polygon, identity)
        return _Candidate(
            identity=identity,
            feature=SpatialFeature(
                geometry=geometry,
                properties=cls._tags(element),
                feature_id=identity,
            ),
        )

    @classmethod
    def _join_member_rings(
        cls,
        segments: list[tuple[str, list[tuple[float, float]]]],
        role: str,
        relation_identity: str,
    ) -> list[list[tuple[float, float]]]:
        pending: tuple[_RingSegment, ...] = tuple(
            (member_id, tuple(coordinates))
            for member_id, coordinates in sorted(segments, key=lambda item: item[0])
        )
        if not pending:
            return []

        _first_id, first_coordinates = pending[0]
        states: list[_RingSearchState] = [
            (first_coordinates, pending[1:], ()),
        ]
        state_count = 0
        branch_count = 0
        complete_count = 0
        first_complete: tuple[_Ring, ...] | None = None
        valid_decompositions: dict[tuple[str, ...], tuple[_Ring, ...]] = {}

        while states:
            state_count += 1
            if state_count > MAX_RING_SEARCH_STATES:
                raise ProviderError(
                    "Overpass relation ring 검색 상태 한도를 초과했습니다: "
                    f"{relation_identity}, role={role}, limit={MAX_RING_SEARCH_STATES}"
                )
            current, remaining, completed = states.pop()

            if current[0] == current[-1]:
                try:
                    validated = tuple(
                        cls._validated_ring(
                            current,
                            f"{relation_identity} {role}",
                            close=False,
                        )
                    )
                except ProviderError:
                    continue
                next_completed = (*completed, validated)
                if remaining:
                    _next_id, next_coordinates = remaining[0]
                    states.append(
                        (next_coordinates, remaining[1:], next_completed)
                    )
                    continue

                complete_count += 1
                if first_complete is None:
                    first_complete = next_completed
                polygon_keys: list[str] = []
                simple = True
                for ring in next_completed:
                    try:
                        polygon = Polygon(ring)
                    except (ShapelyError, TypeError, ValueError):
                        simple = False
                        break
                    if polygon.is_empty or not polygon.is_valid or not polygon.exterior.is_simple:
                        simple = False
                        break
                    polygon_keys.append(normalize(polygon).wkb_hex)
                if simple:
                    key = tuple(sorted(polygon_keys))
                    valid_decompositions.setdefault(key, next_completed)
                continue

            endpoint = current[-1]
            options: list[tuple[str, bool, int, _Ring]] = []
            for index, (member_id, candidate) in enumerate(remaining):
                if candidate[0] == candidate[-1]:
                    continue
                if candidate[0] == endpoint:
                    options.append((member_id, False, index, candidate))
                if candidate[-1] == endpoint:
                    options.append((member_id, True, index, candidate))

            branch_count += len(options)
            if branch_count > MAX_RING_SEARCH_BRANCHES:
                raise ProviderError(
                    "Overpass relation ring 검색 분기 한도를 초과했습니다: "
                    f"{relation_identity}, role={role}, limit={MAX_RING_SEARCH_BRANCHES}"
                )
            for _member_id, reverse, index, candidate in reversed(
                sorted(options, key=lambda item: (item[0], item[1]))
            ):
                oriented = tuple(reversed(candidate)) if reverse else candidate
                next_remaining = remaining[:index] + remaining[index + 1 :]
                states.append(
                    (current + oriented[1:], next_remaining, completed)
                )

        if valid_decompositions:
            selected_key = min(valid_decompositions)
            selected = valid_decompositions[selected_key]
            ordered = sorted(
                selected,
                key=lambda ring: normalize(Polygon(ring)).wkb_hex,
            )
            return [list(ring) for ring in ordered]
        if role == "outer" and complete_count == 1 and first_complete is not None:
            return [list(ring) for ring in first_complete]
        raise ProviderError(
            "Overpass relation member endpoint의 유효한 링 분해를 찾을 수 없습니다: "
            f"{relation_identity}, role={role}"
        )

    @classmethod
    def _relation_polygon_candidate(cls, element: Mapping[str, object]) -> _Candidate:
        identity = cls._element_identity(element, "relation")
        raw_members = element.get("members")
        if not isinstance(raw_members, list):
            raise ProviderError(f"Overpass relation members가 없습니다: {identity}")

        segments: dict[str, list[tuple[str, list[tuple[float, float]]]]] = {
            "outer": [],
            "inner": [],
        }
        member_way_ids: set[str] = set()
        for member in raw_members:
            if not isinstance(member, Mapping):
                raise ProviderError(f"Overpass relation member 형식이 올바르지 않습니다: {identity}")
            role = member.get("role")
            if role not in segments:
                continue
            if member.get("type") != "way":
                raise ProviderError(
                    f"Overpass relation {role} member는 way여야 합니다: {identity}"
                )
            reference = member.get("ref")
            if isinstance(reference, bool) or not isinstance(reference, int) or reference <= 0:
                raise ProviderError(f"Overpass relation member ref가 올바르지 않습니다: {identity}")
            member_identity = f"way/{reference}"
            if member_identity in member_way_ids:
                raise ProviderError(
                    f"Overpass relation member가 중복되었습니다: {identity}, {member_identity}"
                )
            member_way_ids.add(member_identity)
            coordinates = cls._way_coordinates(member.get("geometry"), member_identity)
            segments[role].append((member_identity, coordinates))

        if not segments["outer"]:
            raise ProviderError(f"Overpass relation outer member가 없습니다: {identity}")
        outer_rings = cls._join_member_rings(segments["outer"], "outer", identity)
        inner_rings = cls._join_member_rings(segments["inner"], "inner", identity)

        outer_polygons: list[Polygon] = []
        for ring in outer_rings:
            normalized = cls._normalize_multi_polygon(Polygon(ring), f"{identity} outer")
            outer_polygons.extend(normalized.geoms)

        holes_by_outer: list[list[list[tuple[float, float]]]] = [
            [] for _ in outer_polygons
        ]
        for ring in inner_rings:
            try:
                inner_polygon = Polygon(ring)
            except (ShapelyError, TypeError, ValueError) as exc:
                raise ProviderError(
                    f"Overpass relation inner Polygon을 만들 수 없습니다: {identity}"
                ) from exc
            if inner_polygon.is_empty or not inner_polygon.is_valid:
                raise ProviderError(
                    f"Overpass relation inner Polygon이 올바르지 않습니다: {identity}"
                )
            containing = [
                index
                for index, outer in enumerate(outer_polygons)
                if outer.contains(inner_polygon)
            ]
            if len(containing) != 1:
                raise ProviderError(
                    f"Overpass relation inner 링을 포함하는 outer가 없습니다: {identity}"
                )
            holes_by_outer[containing[0]].append(ring)

        polygons: list[Polygon] = []
        for outer, new_holes in zip(outer_polygons, holes_by_outer):
            existing_holes = [list(interior.coords) for interior in outer.interiors]
            polygons.append(
                Polygon(
                    list(outer.exterior.coords),
                    [*existing_holes, *new_holes],
                )
            )
        geometry = cls._normalize_multi_polygon(MultiPolygon(polygons), identity)
        return _Candidate(
            identity=identity,
            feature=SpatialFeature(
                geometry=geometry,
                properties=cls._tags(element),
                feature_id=identity,
            ),
            member_way_ids=frozenset(member_way_ids),
        )

    @staticmethod
    def _is_power_line(tags: Mapping[str, str]) -> bool:
        return tags.get("power") == "line"

    @staticmethod
    def _is_power_support(tags: Mapping[str, str]) -> bool:
        return tags.get("power") in {"tower", "pole"}

    @staticmethod
    def _is_river_centerline(tags: Mapping[str, str]) -> bool:
        return tags.get("waterway") in {"river", "stream"}

    @staticmethod
    def _is_river_area(tags: Mapping[str, str]) -> bool:
        return (
            tags.get("natural") == "water" and tags.get("water") == "river"
        ) or tags.get("waterway") == "riverbank"

    @classmethod
    def _node_references(cls, element: Mapping[str, object]) -> set[int]:
        identity = cls._element_identity(element, "way")
        raw_nodes = element.get("nodes")
        if not isinstance(raw_nodes, list):
            raise ProviderError(f"Overpass power=line way nodes가 없습니다: {identity}")
        node_ids: set[int] = set()
        for node_id in raw_nodes:
            if isinstance(node_id, bool) or not isinstance(node_id, int) or node_id <= 0:
                raise ProviderError(f"Overpass way node ID가 올바르지 않습니다: {identity}")
            node_ids.add(node_id)
        if not node_ids:
            raise ProviderError(f"Overpass power=line way nodes가 비어 있습니다: {identity}")
        return node_ids

    @classmethod
    def _validate_canonical_source_value(
        cls,
        value: object,
        identity: str,
    ) -> None:
        stack: list[tuple[object, int]] = [(value, 0)]
        seen_containers: set[int] = set()
        scheduled_items = 1

        while stack:
            current, depth = stack.pop()
            if depth > MAX_SOURCE_CANONICAL_DEPTH:
                raise ProviderError(
                    "Overpass source 객체 depth 한도를 초과했습니다: "
                    f"source=osm, id={identity}, limit={MAX_SOURCE_CANONICAL_DEPTH}"
                )
            if current is None or type(current) in {str, bool, int}:
                continue
            if type(current) is float:
                if not isfinite(current):
                    raise ProviderError(
                        "Overpass source 객체에 유한하지 않은 숫자가 있습니다: "
                        f"source=osm, id={identity}"
                    )
                continue
            if type(current) not in {dict, list}:
                raise ProviderError(
                    "Overpass source 객체를 canonical JSON으로 만들 수 없습니다: "
                    f"source=osm, id={identity}"
                )

            container_id = id(current)
            if container_id in seen_containers:
                raise ProviderError(
                    "Overpass source 객체에 순환 또는 공유 container 구조가 있습니다: "
                    f"source=osm, id={identity}"
                )
            seen_containers.add(container_id)

            if type(current) is list:
                children = list(current)
            else:
                children = []
                for key, item in current.items():
                    if type(key) is not str:
                        raise ProviderError(
                            "Overpass source 객체의 JSON key가 문자열이 아닙니다: "
                            f"source=osm, id={identity}"
                        )
                    children.append(item)

            scheduled_items += len(children)
            if scheduled_items > MAX_SOURCE_CANONICAL_ITEMS:
                raise ProviderError(
                    "Overpass source 객체 item 한도를 초과했습니다: "
                    f"source=osm, id={identity}, limit={MAX_SOURCE_CANONICAL_ITEMS}"
                )
            child_depth = depth + 1
            for child in reversed(children):
                stack.append((child, child_depth))

    @classmethod
    def _validate_source_structure(
        cls,
        element: dict[str, object],
        element_type: str,
        identity: str,
    ) -> None:
        cls._tags(element)
        if element_type == "node":
            cls._position(element)
            return
        if element_type == "way":
            cls._way_coordinates(element.get("geometry"), identity)
            if "nodes" not in element:
                return
            raw_nodes = element["nodes"]
            if not isinstance(raw_nodes, list) or not raw_nodes:
                raise ProviderError(
                    "Overpass source way nodes 형식이 올바르지 않습니다: "
                    f"source=osm, id={identity}"
                )
            if any(
                isinstance(node_id, bool)
                or not isinstance(node_id, int)
                or node_id <= 0
                for node_id in raw_nodes
            ):
                raise ProviderError(
                    "Overpass source way node ID가 올바르지 않습니다: "
                    f"source=osm, id={identity}"
                )
            return

        raw_members = element.get("members")
        if not isinstance(raw_members, list):
            raise ProviderError(
                "Overpass source relation members 형식이 올바르지 않습니다: "
                f"source=osm, id={identity}"
            )
        for member in raw_members:
            if type(member) is not dict:
                raise ProviderError(
                    "Overpass source relation member 형식이 올바르지 않습니다: "
                    f"source=osm, id={identity}"
                )
            member_type = member.get("type")
            reference = member.get("ref")
            role = member.get("role")
            if member_type not in {"node", "way", "relation"}:
                raise ProviderError(
                    "Overpass source relation member 유형이 올바르지 않습니다: "
                    f"source=osm, id={identity}"
                )
            if (
                isinstance(reference, bool)
                or not isinstance(reference, int)
                or reference <= 0
            ):
                raise ProviderError(
                    "Overpass source relation member ref가 올바르지 않습니다: "
                    f"source=osm, id={identity}"
                )
            if not isinstance(role, str):
                raise ProviderError(
                    "Overpass source relation member role이 올바르지 않습니다: "
                    f"source=osm, id={identity}"
                )
            member_identity = f"{identity} member {member_type}/{reference}"
            if "geometry" in member:
                cls._way_coordinates(member["geometry"], member_identity)
            elif member_type == "way" and role in {"outer", "inner"}:
                raise ProviderError(
                    "Overpass source relation member Geometry가 없습니다: "
                    f"source=osm, id={identity}, member={member_type}/{reference}"
                )
            if member_type == "node" and ("lat" in member or "lon" in member):
                cls._position(member)

    @classmethod
    def _source_object_fingerprints(
        cls,
        payload: Mapping[str, object],
    ) -> list[tuple[str, str]]:
        raw_elements = payload.get("elements")
        if not isinstance(raw_elements, list):
            raise ProviderError("Overpass 응답의 elements 형식이 올바르지 않습니다.")
        fingerprints: list[tuple[str, str]] = []
        for raw_element in raw_elements:
            if type(raw_element) is not dict:
                raise ProviderError("Overpass source element 형식이 올바르지 않습니다.")
            element_type = raw_element.get("type")
            if element_type not in {"node", "way", "relation"}:
                raise ProviderError("Overpass source element 유형이 올바르지 않습니다.")
            identity = cls._element_identity(raw_element, element_type)
            cls._validate_canonical_source_value(raw_element, identity)
            cls._validate_source_structure(raw_element, element_type, identity)
            try:
                canonical = json.dumps(
                    raw_element,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            except (TypeError, ValueError, OverflowError, RecursionError) as exc:
                raise ProviderError(
                    "Overpass source 객체를 canonical JSON으로 만들 수 없습니다: "
                    f"source=osm, id={identity}"
                ) from exc
            fingerprints.append(
                (identity, hashlib.sha256(canonical).hexdigest())
            )
        return fingerprints

    @classmethod
    def _parse_elements(
        cls,
        definition: LayerDefinition,
        payload: Mapping[str, object],
    ) -> tuple[list[_Candidate], set[str]]:
        raw_elements = payload.get("elements")
        if not isinstance(raw_elements, list):
            raise ProviderError("Overpass 응답의 elements 형식이 올바르지 않습니다.")
        elements: list[Mapping[str, object]] = []
        for element in raw_elements:
            if not isinstance(element, Mapping):
                raise ProviderError("Overpass element 형식이 올바르지 않습니다.")
            if element.get("type") not in {"node", "way", "relation"}:
                raise ProviderError("Overpass element 유형이 올바르지 않습니다.")
            elements.append(element)

        candidates: list[_Candidate] = []
        member_way_ids: set[str] = set()
        if definition.id == "power_line":
            for element in elements:
                if element.get("type") == "way" and cls._is_power_line(cls._tags(element)):
                    candidates.append(cls._line_candidate(element))
        elif definition.id == "power_support":
            eligible_nodes: set[int] = set()
            for element in elements:
                if element.get("type") == "way" and cls._is_power_line(cls._tags(element)):
                    eligible_nodes.update(cls._node_references(element))
            for element in elements:
                if element.get("type") != "node" or not cls._is_power_support(cls._tags(element)):
                    continue
                identity = cls._element_identity(element, "node")
                if int(identity.split("/", 1)[1]) in eligible_nodes:
                    candidates.append(cls._point_candidate(element))
        elif definition.id == "river_centerline":
            for element in elements:
                if element.get("type") == "way" and cls._is_river_centerline(cls._tags(element)):
                    candidates.append(cls._line_candidate(element))
        elif definition.id == "river_area":
            area_relations = [
                element
                for element in elements
                if element.get("type") == "relation"
                and cls._is_river_area(cls._tags(element))
            ]
            for element in area_relations:
                relation = cls._relation_polygon_candidate(element)
                candidates.append(relation)
                member_way_ids.update(relation.member_way_ids)
            for element in elements:
                if element.get("type") != "way":
                    continue
                identity = cls._element_identity(element, "way")
                if identity in member_way_ids:
                    continue
                if cls._is_river_area(cls._tags(element)):
                    candidates.append(cls._way_polygon_candidate(element))
        else:
            raise ProviderError(f"지원하지 않는 Overpass 레이어입니다: {definition.id}")
        return candidates, member_way_ids

    @staticmethod
    def _candidate_fingerprint(candidate: _Candidate) -> str:
        try:
            normalized_wkb = normalize(candidate.feature.geometry).wkb_hex
        except (ShapelyError, TypeError, ValueError) as exc:
            raise ProviderError(
                f"Overpass Geometry를 정규화할 수 없습니다: {candidate.identity}"
            ) from exc
        canonical = json.dumps(
            {
                "geometry": normalized_wkb,
                "properties": candidate.feature.properties,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    @staticmethod
    def _combined_geometry(layer: SpatialLayer):
        geometries = [
            feature.geometry for feature in layer.features if not feature.geometry.is_empty
        ]
        if not geometries:
            raise ProviderError("Overpass 조회 범위를 만들 수 없습니다.")
        return unary_union(geometries)

    @staticmethod
    def _validate_definition(definition: LayerDefinition) -> None:
        expected_geometry = _GEOMETRY_TYPES.get(definition.id)
        if expected_geometry is None:
            raise ProviderError(f"지원하지 않는 Overpass 레이어입니다: {definition.id}")
        if definition.provider != "osm" or definition.service_type != "overpass":
            raise ProviderError(f"Overpass 레이어 정의가 올바르지 않습니다: {definition.id}")
        if definition.geometry_type != expected_geometry:
            raise ProviderError(
                f"Overpass Geometry 유형이 올바르지 않습니다: {definition.id}"
            )
        if normalize_crs(definition.request_crs) != "EPSG:4326" or (
            definition.response_crs is not None
            and normalize_crs(definition.response_crs) != "EPSG:4326"
        ):
            raise ProviderError("Overpass 요청과 응답 좌표계는 EPSG:4326이어야 합니다.")

    def fetch_layer(
        self,
        definition: LayerDefinition,
        aoi: SpatialLayer,
    ) -> DownloadResult:
        context = FetchContext("Overpass")
        try:
            self._validate_definition(definition)
            planner = AoiTilePlanner(
                aoi,
                "EPSG:4326",
                self._tile_size_m,
                self._max_split_depth,
                self._minimum_tile_size_m,
            )
            initial_tiles = planner.initial_tiles()
            context.initial_tile_count = len(initial_tiles)
            context.request_budget = compute_request_budget(
                len(initial_tiles),
                self._request_budget_minimum,
                self._request_budget_per_tile,
                self._request_budget_maximum,
            )
            if not initial_tiles:
                raise ProviderError("Overpass 조회 타일을 만들 수 없습니다.")

            candidates_by_id: dict[str, _Candidate] = {}
            source_origins: dict[
                str,
                tuple[str, tuple[float, float, float, float]],
            ] = {}
            relation_member_way_ids: set[str] = set()
            for tile in initial_tiles:
                context.activate(tile)
                payload = self._payload(
                    self._response_bytes(self._query(definition, tile.request_bounds), context)
                )
                for identity, fingerprint in self._source_object_fingerprints(payload):
                    previous = source_origins.get(identity)
                    if previous is not None and previous[0] != fingerprint:
                        raise ProviderError(
                            "Overpass source 객체 ID의 내용이 요청 영역마다 다릅니다: "
                            f"source=osm, id={identity}, "
                            f"tile={tile.output_bounds}, previous_tile={previous[1]}"
                        )
                    if previous is None:
                        source_origins[identity] = (
                            fingerprint,
                            tile.output_bounds,
                        )
                tile_candidates, tile_member_ids = self._parse_elements(
                    definition,
                    payload,
                )
                for candidate in tile_candidates:
                    candidates_by_id.setdefault(candidate.identity, candidate)
                relation_member_way_ids.update(tile_member_ids)
                context.finish_tile()

            stable_candidates = [
                candidate
                for identity, candidate in sorted(candidates_by_id.items())
                if not (
                    definition.id == "river_area"
                    and identity in relation_member_way_ids
                )
            ]
            candidates: list[_Candidate] = []
            seen_content: set[str] = set()
            for candidate in stable_candidates:
                fingerprint = self._candidate_fingerprint(candidate)
                if fingerprint in seen_content:
                    continue
                seen_content.add(fingerprint)
                candidates.append(candidate)
            request_layer = SpatialLayer(
                name=definition.id,
                display_name=definition.name,
                crs="EPSG:4326",
                geometry_type=definition.geometry_type,
                category=definition.category,
                features=[candidate.feature for candidate in candidates],
                source="osm",
            )
            output_layer = transform_layer(request_layer, aoi.crs)
            aoi_geometry = self._combined_geometry(aoi)
            output_layer.features = [
                feature
                for feature in output_layer.features
                if feature.geometry.intersects(aoi_geometry)
            ]
            return context.success_result(output_layer)
        except (CrsError, ProviderError) as exc:
            return context.failed_result(definition.id, str(exc))
