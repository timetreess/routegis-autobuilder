from __future__ import annotations

import re
import time
from collections.abc import Callable
from math import isfinite
from urllib.parse import unquote
from xml.etree import ElementTree as ET

import httpx
from defusedxml import DefusedXmlException
from defusedxml import ElementTree as DefusedET
from shapely import make_valid
from shapely.errors import ShapelyError
from shapely.geometry import MultiPoint, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from ...errors import CrsError, ProviderError, RetryableProviderError
from ...gis import AoiTilePlanner, compute_request_budget
from ...gis.crs import normalize_crs, transform_layer
from ...models import DownloadResult, LayerDefinition, SpatialFeature, SpatialLayer
from ...security import redact_sensitive_text
from ..download import DEFAULT_MAX_RESPONSE_BYTES, FetchContext, read_response_bytes, retry_delay, split_failed_tile
from ..tls import CERT_GUIDANCE, create_httpx_client, is_certificate_verification_error


ECOLOGY_WFS_ENDPOINT = (
    "https://apis.data.go.kr/B553084/ecoapi/EcologyzmpService/wfs/getEcologyzmpWFS"
)
ECOLOGY_TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504})
ECOLOGY_MAX_FEATURES = 500

WFS_NAMESPACE = "http://www.opengis.net/wfs"
GML_NAMESPACE = "http://www.opengis.net/gml"
ECOLOGY_FEATURE_NAMESPACE = "http://openapi.nie.re.kr"
ECOLOGY_FEATURE_NAMESPACES = frozenset({ECOLOGY_FEATURE_NAMESPACE, "kr.nie-ecobank"})
ECOLOGY_FEATURE_NAME = "tbl_opn_eczm"
ECOLOGY_GEOMETRY_PROPERTY = "geom"
# Only redact URLs carrying a query string (serviceKey etc.). The plain
# namespace URI (http://openapi.nie.re.kr) must stay visible so the
# "expected vs actual feature" error remains debuggable.
_URL_PATTERN = re.compile(r"https?://[^\s'\"<>]+\?[^\s'\"<>]+", flags=re.IGNORECASE)


class EcologyClient:
    def __init__(
        self,
        service_key: str,
        *,
        timeout: float = 30.0,
        retries: int = 3,
        backoff: float = 0.5,
        page_size: int = ECOLOGY_MAX_FEATURES,
        tile_size_m: float = 10_000,
        request_budget_minimum: int = 100,
        request_budget_per_tile: int = 16,
        request_budget_maximum: int = 5_000,
        max_split_depth: int = 32,
        minimum_tile_size_m: float = 50,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        on_progress: Callable[[str], None] | None = None,
    ):
        if not isinstance(service_key, str) or not service_key.strip():
            raise ProviderError("국립생태원 서비스 인증키를 입력해 주세요.")
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

        # The portal offers both Encoding and Decoding keys. HTTPX encodes params.
        # unquote (not unquote_plus) preserves literal '+' in a decoded key.
        self._service_key = unquote(service_key.strip())
        self._retries = max(int(retries), 0)
        self._backoff = max(float(backoff), 0.0)
        self._page_size = min(max(int(page_size), 1), ECOLOGY_MAX_FEATURES)
        self._tile_size_m = float(tile_size_m)
        self._request_budget_minimum = int(request_budget_minimum)
        self._request_budget_per_tile = int(request_budget_per_tile)
        self._request_budget_maximum = int(request_budget_maximum)
        self._max_split_depth = int(max_split_depth)
        self._minimum_tile_size_m = float(minimum_tile_size_m)
        self._max_response_bytes = int(max_response_bytes)
        self._sleep = sleep
        self._on_progress = on_progress
        self._owns_client = client is None
        self._client = client or create_httpx_client(timeout=timeout)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> EcologyClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _redact(self, message: str) -> str:
        redacted = redact_sensitive_text(message, self._service_key) or ""
        return _URL_PATTERN.sub("[redacted URL]", redacted)

    def _params(
        self,
        definition: LayerDefinition,
        bounds: tuple[float, float, float, float],
    ) -> dict[str, object]:
        min_x, min_y, max_x, max_y = bounds
        return {
            "serviceKey": self._service_key,
            "typeName": definition.data_id,
            "bbox": f"{min_x},{min_y},{max_x},{max_y}",
            "maxFeatures": min(self._page_size, ECOLOGY_MAX_FEATURES),
        }

    def _response_bytes(
        self,
        params: dict[str, object],
        context: FetchContext,
    ) -> bytes:
        last_transport_error: httpx.HTTPError | None = None
        for attempt in range(self._retries + 1):
            delay: float | None = None
            context.before_request()
            try:
                with self._client.stream(
                    "GET",
                    ECOLOGY_WFS_ENDPOINT,
                    params=params,
                    follow_redirects=False,
                ) as response:
                    status = response.status_code
                    if status in ECOLOGY_TRANSIENT_STATUSES and attempt < self._retries:
                        delay = retry_delay(response, self._backoff * (2**attempt))
                    else:
                        if 300 <= status < 400:
                            raise ProviderError(
                                f"Ecology 리디렉션 응답을 거부했습니다: HTTP {status}"
                            )
                        content = read_response_bytes(response, self._max_response_bytes, "Ecology")
                        if status >= 400:
                            try:
                                root = DefusedET.fromstring(content, forbid_dtd=True)
                                detail = self._redact(self._service_error(root))
                            except (ET.ParseError, DefusedXmlException):
                                detail = ""
                            suffix = f" · {detail}" if detail else ""
                            error_type = RetryableProviderError if status in {500, 502, 503, 504} else ProviderError
                            raise error_type(
                                f"Ecology 요청에 실패했습니다: HTTP {status}{suffix}"
                            )
                        return content
                if delay is not None:
                    context.notify(f"서버 일시 오류 · {delay:g}초 후 재시도 {attempt + 1}/{self._retries}")
                    self._sleep(delay)
                    continue
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if is_certificate_verification_error(exc):
                    raise ProviderError(
                        "Ecology HTTPS 인증서를 확인할 수 없습니다.\n"
                        f"{CERT_GUIDANCE}\n"
                        f"상세: {self._redact(str(exc))}"
                    ) from exc
                last_transport_error = exc
                if attempt < self._retries:
                    self._sleep(self._backoff * (2**attempt))
                    continue
                break
            except httpx.HTTPError as exc:
                if is_certificate_verification_error(exc):
                    raise ProviderError(
                        "Ecology HTTPS 인증서를 확인할 수 없습니다.\n"
                        f"{CERT_GUIDANCE}\n"
                        f"상세: {self._redact(str(exc))}"
                    ) from exc
                last_transport_error = exc
                break

        if last_transport_error is None:
            raise ProviderError("Ecology 요청에 실패했습니다.")
        detail = self._redact(str(last_transport_error))
        error_type = RetryableProviderError if isinstance(last_transport_error, httpx.TimeoutException) else ProviderError
        raise error_type(
            f"Ecology 요청에 실패했습니다: {type(last_transport_error).__name__}: {detail}"
        ) from last_transport_error

    def _parse_collection(self, content: bytes) -> ET.Element:
        try:
            root = DefusedET.fromstring(content, forbid_dtd=True)
        except DefusedXmlException as exc:
            raise ProviderError(
                "Ecology XML 응답에 허용되지 않는 DTD 또는 엔터티 선언이 있습니다."
            ) from exc
        except ET.ParseError as exc:
            raise ProviderError(
                f"Ecology XML 응답을 읽을 수 없습니다: {self._redact(str(exc))}"
            ) from exc

        detail = self._service_error(root)
        if detail:
            raise ProviderError(f"Ecology 서비스 오류: {self._redact(detail)}")

        namespace, local_name = self._qualified_name(root.tag)
        if namespace != WFS_NAMESPACE or local_name != "FeatureCollection":
            raise ProviderError("Ecology 응답에서 WFS FeatureCollection을 찾을 수 없습니다.")
        return root

    @classmethod
    def _children_named(
        cls,
        parent: ET.Element,
        namespace: str,
        local_name: str,
    ) -> list[ET.Element]:
        return [
            child
            for child in parent
            if cls._qualified_name(child.tag) == (namespace, local_name)
        ]

    @classmethod
    def _feature_members(cls, root: ET.Element) -> list[ET.Element]:
        return cls._children_named(root, GML_NAMESPACE, "featureMember")

    def _response_crs(self, geometry: ET.Element, expected: str) -> str:
        supplied_name = geometry.attrib.get("srsName", "").strip()
        if not supplied_name:
            raise ProviderError("Ecology Geometry 좌표계(srsName)가 없습니다.")
        try:
            supplied = normalize_crs(supplied_name)
            normalized_expected = normalize_crs(expected)
        except CrsError as exc:
            raise ProviderError(
                f"Ecology Geometry 좌표계를 확인할 수 없습니다: {self._redact(supplied_name)}"
            ) from exc
        if supplied != normalized_expected:
            raise ProviderError(
                f"Ecology Geometry 좌표계가 요청과 다릅니다: "
                f"{supplied} (예상 {normalized_expected})"
            )
        return supplied

    @classmethod
    def _coordinate_tuples(
        cls,
        coordinates: ET.Element,
        ring_name: str,
    ) -> list[tuple[float, float]]:
        text = coordinates.text or ""
        if not text.strip():
            raise ProviderError(f"Ecology {ring_name} 링 좌표가 비어 있습니다.")

        decimal = coordinates.attrib.get("decimal", ".")
        coordinate_separator = coordinates.attrib.get("cs", ",")
        tuple_separator = coordinates.attrib.get("ts", " ")
        if not decimal or not coordinate_separator or not tuple_separator:
            raise ProviderError(f"Ecology {ring_name} 링 좌표 구분자가 올바르지 않습니다.")
        if len({decimal, coordinate_separator, tuple_separator}) != 3:
            raise ProviderError(f"Ecology {ring_name} 링 좌표 구분자가 서로 겹칩니다.")

        if tuple_separator.isspace():
            raw_tuples = text.split()
        else:
            raw_tuples = [item.strip() for item in text.strip().split(tuple_separator)]
            if any(not item for item in raw_tuples):
                raise ProviderError(f"Ecology {ring_name} 링 좌표 tuple이 비어 있습니다.")

        positions: list[tuple[float, float]] = []
        for raw_tuple in raw_tuples:
            ordinates = [item.strip() for item in raw_tuple.split(coordinate_separator)]
            if len(ordinates) != 2 or any(not ordinate for ordinate in ordinates):
                raise ProviderError(
                    f"Ecology {ring_name} 링 좌표 tuple은 두 값이어야 합니다."
                )
            if decimal != ".":
                ordinates = [ordinate.replace(decimal, ".") for ordinate in ordinates]
            try:
                position = (float(ordinates[0]), float(ordinates[1]))
            except ValueError as exc:
                raise ProviderError(
                    f"Ecology {ring_name} 링 좌표를 숫자로 읽을 수 없습니다."
                ) from exc
            if not all(isfinite(value) for value in position):
                raise ProviderError(f"Ecology {ring_name} 링 좌표는 유한해야 합니다.")
            positions.append(position)

        distinct_positions = set(positions)
        if len(distinct_positions) < 3:
            raise ProviderError(
                f"Ecology {ring_name} 링에는 서로 다른 좌표가 세 개 이상 필요합니다."
            )
        try:
            if MultiPoint(list(distinct_positions)).convex_hull.area <= 0:
                raise ProviderError(f"Ecology {ring_name} 링이 퇴화했습니다.")
        except ShapelyError as exc:
            raise ProviderError(f"Ecology {ring_name} 링을 만들 수 없습니다.") from exc
        if positions[0] != positions[-1]:
            positions.append(positions[0])
        return positions

    @classmethod
    def _parse_ring(
        cls,
        boundary: ET.Element,
        ring_name: str,
    ) -> list[tuple[float, float]]:
        children = list(boundary)
        if (
            len(children) != 1
            or cls._qualified_name(children[0].tag) != (GML_NAMESPACE, "LinearRing")
        ):
            raise ProviderError(
                f"Ecology {ring_name} 경계에는 GML LinearRing 하나가 필요합니다."
            )
        ring_children = list(children[0])
        if (
            len(ring_children) != 1
            or cls._qualified_name(ring_children[0].tag) != (GML_NAMESPACE, "coordinates")
        ):
            raise ProviderError(
                f"Ecology {ring_name} 링에는 GML coordinates 하나가 필요합니다."
            )
        return cls._coordinate_tuples(ring_children[0], ring_name)

    @classmethod
    def _parse_polygon(cls, element: ET.Element) -> Polygon:
        outer_boundaries = cls._children_named(
            element,
            GML_NAMESPACE,
            "outerBoundaryIs",
        )
        inner_boundaries = cls._children_named(
            element,
            GML_NAMESPACE,
            "innerBoundaryIs",
        )
        if len(outer_boundaries) != 1:
            raise ProviderError("Ecology Polygon에는 외부 경계가 정확히 하나 필요합니다.")
        allowed_boundaries = len(outer_boundaries) + len(inner_boundaries)
        if len(list(element)) != allowed_boundaries:
            raise ProviderError("Ecology Polygon 경계 형식이 올바르지 않습니다.")

        exterior = cls._parse_ring(outer_boundaries[0], "외부")
        interiors = [cls._parse_ring(boundary, "내부") for boundary in inner_boundaries]
        try:
            polygon = Polygon(exterior, interiors)
        except (ShapelyError, TypeError, ValueError) as exc:
            raise ProviderError("Ecology Polygon을 만들 수 없습니다.") from exc
        if polygon.is_empty:
            raise ProviderError("Ecology Polygon이 비어 있습니다.")
        return polygon

    @classmethod
    def _normalize_multi_polygon(cls, geometry: MultiPolygon) -> MultiPolygon:
        try:
            repaired = geometry if geometry.is_valid else make_valid(geometry)
        except (ShapelyError, TypeError, ValueError) as exc:
            raise ProviderError("Ecology MultiPolygon을 복구할 수 없습니다.") from exc

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
        if incompatible:
            types = ", ".join(sorted(set(incompatible)))
            raise ProviderError(
                f"Ecology MultiPolygon을 복구할 수 없습니다: 호환되지 않는 부분 {types}"
            )
        if not polygons:
            raise ProviderError("Ecology MultiPolygon을 복구할 수 없습니다.")
        try:
            normalized = MultiPolygon(polygons)
        except (ShapelyError, TypeError, ValueError) as exc:
            raise ProviderError("Ecology MultiPolygon을 복구할 수 없습니다.") from exc
        if normalized.is_empty or not normalized.is_valid:
            raise ProviderError("Ecology MultiPolygon을 복구할 수 없습니다.")
        return normalized

    def _parse_multi_polygon(
        self,
        element: ET.Element,
        expected_crs: str,
    ) -> tuple[MultiPolygon, str]:
        response_crs = self._response_crs(element, expected_crs)
        members = self._children_named(element, GML_NAMESPACE, "polygonMember")
        if not members:
            raise ProviderError("Ecology MultiPolygon에 polygonMember가 없습니다.")
        if len(list(element)) != len(members):
            raise ProviderError("Ecology MultiPolygon member 형식이 올바르지 않습니다.")

        polygons: list[Polygon] = []
        for member in members:
            children = list(member)
            if (
                len(children) != 1
                or self._qualified_name(children[0].tag) != (GML_NAMESPACE, "Polygon")
            ):
                raise ProviderError(
                    "Ecology polygonMember에는 GML Polygon 하나가 필요합니다."
                )
            polygons.append(self._parse_polygon(children[0]))
        return self._normalize_multi_polygon(MultiPolygon(polygons)), response_crs

    def _parse_feature_member(
        self,
        member: ET.Element,
        expected_crs: str,
    ) -> tuple[SpatialFeature, str]:
        feature_children = list(member)
        if len(feature_children) != 1:
            raise ProviderError(
                "Ecology featureMember에는 Feature 하나가 정확히 필요합니다."
            )
        feature = feature_children[0]
        actual_namespace, actual_name = self._qualified_name(feature.tag)
        if actual_namespace not in ECOLOGY_FEATURE_NAMESPACES or actual_name != ECOLOGY_FEATURE_NAME:
            actual_label = actual_name or feature.tag
            if actual_namespace and actual_namespace != ECOLOGY_FEATURE_NAMESPACE:
                actual_label = f"{actual_label} (namespace={actual_namespace})"
            raise ProviderError(
                "Ecology featureMember의 Feature가 올바르지 않습니다: "
                f"예상 {ECOLOGY_FEATURE_NAME}, 실제 {actual_label}"
            )

        fid = feature.attrib.get("fid", "").strip()
        if not fid:
            raise ProviderError("Ecology Feature에 비어 있지 않은 fid가 필요합니다.")

        geometry_properties = self._children_named(
            feature,
            actual_namespace,
            ECOLOGY_GEOMETRY_PROPERTY,
        )
        if len(geometry_properties) != 1:
            raise ProviderError(
                "Ecology Feature에는 공식 geom Geometry 속성이 정확히 하나 필요합니다."
            )
        geometry_property = geometry_properties[0]
        geometry_children = list(geometry_property)
        if (
            len(geometry_children) != 1
            or self._qualified_name(geometry_children[0].tag)
            != (GML_NAMESPACE, "MultiPolygon")
        ):
            raise ProviderError(
                "Ecology Geometry geom 속성에는 직접 GML MultiPolygon 하나가 필요합니다."
            )
        geometry_element = geometry_children[0]
        geometry, response_crs = self._parse_multi_polygon(geometry_element, expected_crs)

        properties: dict[str, str] = {}
        for child in feature:
            if child is geometry_property:
                continue
            property_namespace, property_name = self._qualified_name(child.tag)
            if property_namespace != actual_namespace or list(child):
                raise ProviderError(
                    f"Ecology Feature 속성 형식이 올바르지 않습니다: {property_name}"
                )
            if property_name in properties:
                raise ProviderError(f"Ecology Feature 속성이 중복되었습니다: {property_name}")
            properties[property_name] = (child.text or "").strip()

        return SpatialFeature(geometry=geometry, properties=properties, feature_id=fid), response_crs

    def _parse_features(
        self,
        root: ET.Element,
        expected_crs: str,
    ) -> tuple[list[SpatialFeature], str]:
        features: list[SpatialFeature] = []
        response_crs = normalize_crs(expected_crs)
        for member in self._feature_members(root):
            feature, parsed_crs = self._parse_feature_member(
                member,
                expected_crs,
            )
            if parsed_crs != response_crs:
                raise ProviderError(
                    f"Ecology 응답 안의 Geometry 좌표계가 서로 다릅니다: {parsed_crs}"
                )
            features.append(feature)
        return features, response_crs

    @staticmethod
    def _merge_features(
        target: dict[str, SpatialFeature],
        features: list[SpatialFeature],
        tile_bounds: tuple[float, float, float, float],
    ) -> None:
        for feature in features:
            fid = feature.feature_id
            existing = target.get(fid)
            if existing is None:
                target[fid] = feature
                continue
            if (
                existing.properties != feature.properties
                or not existing.geometry.equals(feature.geometry)
            ):
                raise ProviderError(
                    "Ecology 같은 fid의 내용이 서로 다릅니다: "
                    f"fid={fid}, bbox={tile_bounds}"
                )

    @staticmethod
    def _qualified_name(tag: str) -> tuple[str, str]:
        if tag.startswith("{") and "}" in tag:
            namespace, local_name = tag[1:].split("}", 1)
            return namespace, local_name
        return "", tag.rsplit(":", 1)[-1]

    @classmethod
    def _service_error(cls, root: ET.Element) -> str:
        for element in root.iter():
            _, local_name = cls._qualified_name(element.tag)
            if local_name == "cmmMsgHeader":
                fields = {
                    cls._qualified_name(child.tag)[1]: (child.text or "").strip()
                    for child in element
                }
                detail = " · ".join(
                    fields[name] for name in ("returnReasonCode", "errMsg", "returnAuthMsg")
                    if fields.get(name)
                )
                if detail:
                    return detail
            if local_name not in {"Exception", "ExceptionText", "ServiceException"}:
                continue
            code = element.attrib.get("exceptionCode") or element.attrib.get("code")
            locator = element.attrib.get("locator")
            text = " ".join(part.strip() for part in element.itertext() if part.strip())
            return " · ".join(part for part in (code, locator, text) if part)
        return ""

    @staticmethod
    def _combined_geometry(layer: SpatialLayer):
        geometries = [feature.geometry for feature in layer.features if not feature.geometry.is_empty]
        if not geometries:
            raise ProviderError("Ecology 조회 범위를 만들 수 없습니다.")
        return unary_union(geometries)

    def fetch_layer(self, definition: LayerDefinition, aoi: SpatialLayer) -> DownloadResult:
        context = FetchContext("Ecology", on_progress=self._on_progress)
        try:
            planner = AoiTilePlanner(
                aoi,
                definition.request_crs,
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
                raise ProviderError("Ecology 조회 타일을 만들 수 없습니다.")

            expected_crs = definition.response_crs or definition.request_crs
            parsed_features: dict[str, SpatialFeature] = {}
            response_crs = normalize_crs(expected_crs)
            pending = list(initial_tiles)
            next_tile = 0
            while next_tile < len(pending):
                tile = pending[next_tile]
                next_tile += 1
                context.activate(tile)
                try:
                    root = self._parse_collection(
                        self._response_bytes(self._params(definition, tile.request_bounds), context)
                    )
                except RetryableProviderError as exc:
                    pending[next_tile:next_tile] = split_failed_tile(planner, tile, context, exc)
                    continue
                member_count = len(self._feature_members(root))
                if member_count >= self._page_size:
                    children = planner.split(tile)
                    if not children:
                        raise ProviderError(
                            "Ecology WFS 밀집 영역을 더 나눌 수 없습니다: "
                            f"layer={definition.id}, bbox={tile.output_bounds}, "
                            f"depth={tile.depth}, returned={member_count}"
                        )
                    pending.extend(children)
                    context.active_tile = None
                    continue
                tile_features, response_crs = self._parse_features(
                    root,
                    expected_crs,
                )
                self._merge_features(
                    parsed_features,
                    tile_features,
                    tile.output_bounds,
                )
                context.finish_tile()

            request_layer = SpatialLayer(
                name=definition.id,
                display_name=definition.name,
                crs=response_crs,
                geometry_type="MultiPolygon",
                category=definition.category,
                features=[parsed_features[fid] for fid in sorted(parsed_features)],
                source="ecology",
            )
            output_layer = transform_layer(request_layer, aoi.crs)
            aoi_geometry = self._combined_geometry(aoi)
            output_layer.features = [
                feature
                for feature in output_layer.features
                if feature.geometry.intersects(aoi_geometry)
            ]
            return context.success_result(output_layer)
        except CrsError as exc:
            error = f"Ecology 좌표계를 처리할 수 없습니다: {self._redact(str(exc))}"
            return context.failed_result(definition.id, error)
        except ProviderError as exc:
            return context.failed_result(definition.id, self._redact(str(exc)))
