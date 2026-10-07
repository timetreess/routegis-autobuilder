from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from math import isfinite
from typing import Any
from xml.etree import ElementTree as ET

import httpx
from defusedxml import DefusedXmlException
from defusedxml import ElementTree as DefusedET
from shapely import make_valid
from shapely.errors import ShapelyError
from shapely.geometry import MultiLineString, MultiPoint, MultiPolygon, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from ...errors import CrsError, ProviderError
from ...gis import AoiTile, AoiTilePlanner, compute_request_budget
from ...gis.crs import normalize_crs, transform_layer
from ...models import DownloadResult, LayerDefinition, SpatialFeature, SpatialLayer
from ...security import redact_sensitive_text


TRANSIENT_STATUS = {429, 500, 502, 503, 504}
WFS_ENDPOINT = "https://api.vworld.kr/req/wfs"
DATA_ENDPOINT = "https://api.vworld.kr/req/data"
WFS_FEATURE_LIMIT = 1000
DEFAULT_MAX_RESPONSE_BYTES = 64 * 1024 * 1024
GEOMETRY_TYPES = {
    "PolygonPropertyType": "MultiPolygon",
    "MultiSurfacePropertyType": "MultiPolygon",
    "SurfacePropertyType": "MultiPolygon",
    "LineStringPropertyType": "MultiLineString",
    "CurvePropertyType": "MultiLineString",
    "MultiCurvePropertyType": "MultiLineString",
    "PointPropertyType": "MultiPoint",
    "MultiPointPropertyType": "MultiPoint",
}


@dataclass
class _FetchContext:
    initial_tile_count: int
    request_budget: int
    request_count: int = 0
    successful_tile_count: int = 0
    active_tile: AoiTile | None = None
    failed_tile_bbox: tuple[float, float, float, float] | None = None

    def activate(self, tile: AoiTile) -> None:
        self.active_tile = tile

    def finish_tile(self) -> None:
        self.successful_tile_count += 1
        self.active_tile = None

    def before_request(self) -> None:
        if self.request_count >= self.request_budget:
            self.capture_failure()
            raise ProviderError(
                f"VWorld 레이어 요청 예산을 초과했습니다: budget={self.request_budget}"
            )
        self.request_count += 1

    def capture_failure(self) -> None:
        if self.failed_tile_bbox is None and self.active_tile is not None:
            self.failed_tile_bbox = self.active_tile.output_bounds


class VWorldClient:
    def __init__(
        self,
        api_key: str,
        *,
        domain: str | None = None,
        timeout: float = 30.0,
        retries: int = 3,
        backoff: float = 0.5,
        page_size: int = 1000,
        land_ownership_tile_size_m: float = 5000,
        default_tile_size_m: float = 10000,
        request_budget_minimum: int = 100,
        request_budget_per_tile: int = 16,
        request_budget_maximum: int = 5000,
        max_split_depth: int = 32,
        minimum_tile_size_m: float = 50,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if not api_key.strip():
            raise ProviderError("VWorld API Key를 입력해 주세요.")
        for name, value in (
            ("land_ownership_tile_size_m", land_ownership_tile_size_m),
            ("default_tile_size_m", default_tile_size_m),
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
                f"max_response_bytes must be between 1 and {DEFAULT_MAX_RESPONSE_BYTES}"
            )
        self._api_key = api_key.strip()
        self._domain = domain
        self._retries = max(retries, 0)
        self._backoff = max(backoff, 0.0)
        self._page_size = max(page_size, 1)
        self._land_ownership_tile_size_m = land_ownership_tile_size_m
        self._default_tile_size_m = default_tile_size_m
        self._request_budget_minimum = request_budget_minimum
        self._request_budget_per_tile = request_budget_per_tile
        self._request_budget_maximum = request_budget_maximum
        self._max_split_depth = max_split_depth
        self._minimum_tile_size_m = minimum_tile_size_m
        self._max_response_bytes = max_response_bytes
        self._sleep = sleep
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=httpx.Timeout(timeout), follow_redirects=False)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> VWorldClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _redact(self, message: str) -> str:
        return redact_sensitive_text(message, self._api_key, redact_urls=False) or ""

    def _response(
        self,
        endpoint: str,
        params: dict[str, Any],
        context: _FetchContext | None = None,
    ) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(self._retries + 1):
            retry_delay: float | None = None
            if context is not None:
                context.before_request()
            try:
                with self._client.stream(
                    "GET",
                    endpoint,
                    params=params,
                    follow_redirects=False,
                ) as response:
                    if response.status_code in TRANSIENT_STATUS and attempt < self._retries:
                        retry_delay = self._backoff * (2**attempt)
                    else:
                        response.raise_for_status()
                        declared_size = response.headers.get("content-length")
                        if declared_size is not None:
                            try:
                                if int(declared_size) > self._max_response_bytes:
                                    raise ProviderError("VWorld 응답 크기 제한을 초과했습니다.")
                            except ValueError:
                                pass

                        content = bytearray()
                        for chunk in response.iter_bytes():
                            if len(content) + len(chunk) > self._max_response_bytes:
                                raise ProviderError("VWorld 응답 크기 제한을 초과했습니다.")
                            content.extend(chunk)
                        headers = [
                            (name, value)
                            for name, value in response.headers.multi_items()
                            if name.lower()
                            not in {"content-encoding", "content-length", "transfer-encoding"}
                        ]
                        return httpx.Response(
                            response.status_code,
                            headers=headers,
                            content=bytes(content),
                            request=response.request,
                            extensions=response.extensions,
                        )
                if retry_delay is not None:
                    self._sleep(retry_delay)
                    continue
            except (httpx.RequestError, httpx.HTTPStatusError) as exc:
                last_error = exc
                transient = isinstance(exc, (httpx.TimeoutException, httpx.TransportError))
                if transient and attempt < self._retries:
                    self._sleep(self._backoff * (2**attempt))
                    continue
                break
        detail = self._redact(str(last_error or "알 수 없는 오류"))
        raise ProviderError(f"VWorld 요청에 실패했습니다: {detail}") from last_error

    def _request(
        self,
        endpoint: str,
        params: dict[str, Any],
        context: _FetchContext | None = None,
    ) -> dict[str, Any]:
        response = self._response(endpoint, params, context)
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            service_error = self._xml_service_error(response.content)
            if service_error:
                raise ProviderError(f"VWorld 서비스 오류: {self._redact(service_error)}") from exc
            raise ProviderError(f"VWorld JSON 응답을 읽을 수 없습니다: {self._redact(str(exc))}") from exc
        if not isinstance(payload, dict):
            raise ProviderError("VWorld 응답이 JSON 객체가 아닙니다.")
        return payload

    @classmethod
    def _xml_service_error(cls, content: bytes) -> str | None:
        try:
            root = cls._parse_xml(content)
        except ET.ParseError:
            return None
        for element in root.iter():
            if cls._local_name(element.tag) not in {"Exception", "ServiceException"}:
                continue
            code = element.attrib.get("exceptionCode") or element.attrib.get("code")
            locator = element.attrib.get("locator")
            message = " ".join(text.strip() for text in element.itertext() if text.strip())
            parts = [part for part in (code, locator, message) if part]
            if parts:
                return " · ".join(parts)
        return None

    @staticmethod
    def _parse_xml(content: bytes) -> ET.Element:
        try:
            return DefusedET.fromstring(content, forbid_dtd=True)
        except DefusedXmlException as exc:
            raise ProviderError("VWorld XML 응답에 허용되지 않는 DTD 또는 엔터티 선언이 있습니다.") from exc

    def _request_xml(
        self,
        endpoint: str,
        params: dict[str, Any],
        context: _FetchContext | None = None,
    ) -> ET.Element:
        try:
            return self._parse_xml(self._response(endpoint, params, context).content)
        except ET.ParseError as exc:
            raise ProviderError(f"VWorld XML 응답을 읽을 수 없습니다: {self._redact(str(exc))}") from exc

    @staticmethod
    def _local_name(tag: str) -> str:
        return tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1]

    @classmethod
    def _child_text(cls, parent: ET.Element, name: str) -> str:
        for child in parent:
            if cls._local_name(child.tag) == name and child.text:
                return child.text.strip()
        return ""

    def _wfs_operation_params(self, request: str, **params: Any) -> dict[str, Any]:
        return {
            "service": "WFS",
            "version": "1.1.0",
            "request": request,
            "key": self._api_key,
            **({"domain": self._domain} if self._domain else {}),
            **params,
        }

    @staticmethod
    def _endpoint(definition: LayerDefinition) -> str:
        if definition.provider != "vworld":
            raise ProviderError(
                f"VWorld 공급자 정의가 아닙니다: {definition.provider}"
            )
        if definition.service_type == "wfs":
            return WFS_ENDPOINT
        if definition.service_type == "data":
            return DATA_ENDPOINT
        raise ProviderError(
            f"VWorld 서비스 유형을 지원하지 않습니다: {definition.service_type}"
        )

    def discover_layers(self) -> list[LayerDefinition]:
        root = self._request_xml(WFS_ENDPOINT, self._wfs_operation_params("GetCapabilities"))
        definitions: list[LayerDefinition] = []
        for element in root.iter():
            if self._local_name(element.tag) != "FeatureType":
                continue
            data_id = self._child_text(element, "Name")
            if not data_id:
                continue
            name = self._child_text(element, "Title") or data_id
            definitions.append(
                LayerDefinition(
                    id=data_id,
                    name=name,
                    provider="vworld",
                    service_type="wfs",
                    endpoint=WFS_ENDPOINT,
                    data_id=data_id,
                    geometry_type="Unknown",
                    category="facility",
                    request_crs="EPSG:4326",
                    response_crs="EPSG:4326",
                    publisher="국토교통부 공간정보 오픈플랫폼(VWorld)",
                    dataset_id=data_id,
                    license_name="VWorld OpenAPI 이용조건",
                    license_url="https://www.vworld.kr/dev/v4dv_wmsguide2_s001.do",
                )
            )
        if not definitions:
            detail = next(
                (
                    element.text.strip()
                    for element in root.iter()
                    if self._local_name(element.tag) in {"ExceptionText", "ServiceException"} and element.text
                ),
                "",
            )
            suffix = f": {self._redact(detail)}" if detail else ""
            raise ProviderError(f"VWorld WFS 레이어 목록을 찾을 수 없습니다{suffix}")
        return definitions

    def describe_feature_type(self, definition: LayerDefinition) -> str:
        return self._describe_feature_type(definition)

    def _describe_feature_type(
        self,
        definition: LayerDefinition,
        context: _FetchContext | None = None,
    ) -> str:
        root = self._request_xml(
            self._endpoint(definition),
            self._wfs_operation_params("DescribeFeatureType", typeName=definition.data_id),
            context,
        )
        for element in root.iter():
            if self._local_name(element.tag) != "element":
                continue
            property_type = element.attrib.get("type", "").rsplit(":", 1)[-1]
            geometry_type = GEOMETRY_TYPES.get(property_type)
            if geometry_type:
                return geometry_type
        raise ProviderError(f"VWorld Geometry 유형을 확인할 수 없습니다: {definition.name}")

    def _params(
        self,
        definition: LayerDefinition,
        bounds: tuple[float, float, float, float],
        page: int,
    ) -> dict[str, Any]:
        min_x, min_y, max_x, max_y = bounds
        domain = {"domain": self._domain} if self._domain else {}
        if definition.service_type == "wfs":
            return {
                "service": "WFS",
                "version": "1.1.0",
                "request": "GetFeature",
                "typename": definition.data_id,
                "bbox": f"{min_x},{min_y},{max_x},{max_y}",
                "srsName": definition.request_crs,
                "output": "application/json",
                "maxFeatures": min(self._page_size, WFS_FEATURE_LIMIT),
                "key": self._api_key,
                **domain,
            }
        return {
            "service": "data",
            "version": "2.0",
            "request": "getfeature",
            "format": "json",
            "data": definition.data_id,
            "geometry": "true",
            "attribute": "true",
            "crs": definition.request_crs,
            "geomFilter": f"BOX({min_x},{min_y},{max_x},{max_y})",
            "size": self._page_size,
            "page": page,
            "key": self._api_key,
            **domain,
        }

    @staticmethod
    def _feature_collection(payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("type") == "FeatureCollection":
            return payload
        response = payload.get("response")
        if isinstance(response, dict):
            status = response.get("status")
            if status == "ERROR":
                error = response.get("error")
                if isinstance(error, Mapping):
                    detail = error.get("text")
                    if detail is None or detail == "":
                        detail = error.get("message")
                else:
                    detail = error
                message = str(detail).strip() if isinstance(detail, (str, int, float)) else ""
                raise ProviderError(message or "VWorld API 오류")
            result = response.get("result")
            if isinstance(result, dict) and isinstance(result.get("featureCollection"), dict):
                return result["featureCollection"]
        raise ProviderError("VWorld 응답에서 FeatureCollection을 찾을 수 없습니다.")

    @staticmethod
    def _fingerprint(features: list[dict[str, Any]]) -> str:
        encoded = json.dumps(features, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _feature_key(feature: dict[str, Any]) -> str:
        identifier = feature.get("id")
        if identifier is not None:
            return f"id:{identifier}"
        canonical = json.dumps(
            {"geometry": feature.get("geometry"), "properties": feature.get("properties")},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return f"hash:{hashlib.sha256(canonical).hexdigest()}"

    @staticmethod
    def _feature_content_hash(feature: dict[str, Any]) -> str:
        canonical = json.dumps(
            {"geometry": feature.get("geometry"), "properties": feature.get("properties")},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    @classmethod
    def _merge_raw_features(
        cls,
        target: dict[str, dict[str, Any]],
        features: list[Any],
        *,
        content_identity: bool = False,
        seen_ids: dict[str, str] | None = None,
    ) -> None:
        for feature in features:
            if not isinstance(feature, dict):
                continue
            content_hash = cls._feature_content_hash(feature)
            identifier = feature.get("id")
            if seen_ids is not None and identifier is not None:
                identifier = str(identifier)
                previous_hash = seen_ids.get(identifier)
                if previous_hash is not None and previous_hash != content_hash:
                    raise ProviderError(f"VWorld 객체 ID의 내용이 요청 영역마다 다릅니다: {identifier}")
                seen_ids[identifier] = content_hash
            key = f"hash:{content_hash}" if content_identity else cls._feature_key(feature)
            existing = target.get(key)
            if existing is None:
                target[key] = feature
            elif cls._feature_content_hash(existing) != cls._feature_content_hash(feature):
                raise ProviderError(f"VWorld 객체 ID의 내용이 요청 영역마다 다릅니다: {key}")

    @staticmethod
    def _reported_total(collection: dict[str, Any]) -> int | None:
        for name in ("numberMatched", "totalFeatures"):
            value = collection.get(name)
            if isinstance(value, bool):
                continue
            try:
                total = int(value)
            except (TypeError, ValueError):
                continue
            if total >= 0:
                return total
        return None

    @staticmethod
    def _combined_geometry(layer: SpatialLayer):
        geometries = [feature.geometry for feature in layer.features if not feature.geometry.is_empty]
        if not geometries:
            raise ProviderError("VWorld 조회 범위를 만들 수 없습니다.")
        return unary_union(geometries)

    @classmethod
    def _repair_geometry(cls, geometry: BaseGeometry, geometry_type: str) -> BaseGeometry:
        families = {
            "Polygon": ("Polygon", MultiPolygon),
            "MultiPolygon": ("Polygon", MultiPolygon),
            "LineString": ("LineString", MultiLineString),
            "MultiLineString": ("LineString", MultiLineString),
            "Point": ("Point", MultiPoint),
            "MultiPoint": ("Point", MultiPoint),
        }
        family = families.get(geometry_type)
        if family is None:
            raise ProviderError(f"VWorld Geometry 유형을 처리할 수 없습니다: {geometry_type}")
        expected, constructor = family
        compatible_types = {expected, f"Multi{expected}"}
        if geometry.geom_type not in compatible_types:
            raise ProviderError(
                f"VWorld Geometry 유형이 요청 레이어와 다릅니다: "
                f"{geometry.geom_type} (예상 {geometry_type})"
            )

        try:
            repaired = geometry if geometry.is_valid else make_valid(geometry)

            parts: list[BaseGeometry] = []
            incompatible_parts: list[str] = []

            def collect(candidate: BaseGeometry) -> None:
                if candidate.geom_type == expected:
                    parts.append(candidate)
                elif candidate.geom_type in {f"Multi{expected}", "GeometryCollection"}:
                    for child in candidate.geoms:
                        collect(child)
                else:
                    incompatible_parts.append(candidate.geom_type)

            collect(repaired)
            if incompatible_parts:
                part_types = ", ".join(sorted(set(incompatible_parts)))
                raise ProviderError(
                    f"VWorld Geometry를 복구할 수 없습니다: {geometry_type} "
                    f"(호환되지 않는 부분: {part_types})"
                )
            if not parts:
                raise ProviderError(
                    f"VWorld Geometry를 복구할 수 없습니다: {geometry_type}"
                )
            normalized = (
                constructor(parts)
                if geometry_type.startswith("Multi") or len(parts) > 1
                else parts[0]
            )
            if normalized.is_empty or not normalized.is_valid:
                raise ProviderError(
                    f"VWorld Geometry를 복구할 수 없습니다: {geometry_type}"
                )
            return normalized
        except (ShapelyError, TypeError, ValueError) as exc:
            raise ProviderError(
                f"VWorld Geometry를 복구할 수 없습니다: {geometry_type}"
            ) from exc

    def _validate_raw_feature_geometries(
        self,
        features: list[Any],
        definition: LayerDefinition,
    ) -> None:
        malformed_count = 0
        for feature in features:
            if not isinstance(feature, dict):
                continue
            geometry_data = feature.get("geometry")
            if not isinstance(geometry_data, dict):
                malformed_count += 1
                continue
            try:
                geometry = shape(geometry_data)
            except (TypeError, ValueError, ShapelyError) as exc:
                raise ProviderError(f"VWorld Geometry를 읽을 수 없습니다: {definition.name}") from exc
            self._repair_geometry(geometry, definition.geometry_type)
        if malformed_count:
            raise ProviderError(f"VWorld 응답에 Geometry가 없는 객체가 {malformed_count}개 있습니다.")

    def _response_crs(self, collection: dict[str, Any], expected: str) -> str:
        crs = collection.get("crs")
        if not isinstance(crs, dict):
            return normalize_crs(expected)
        properties = crs.get("properties")
        name = properties.get("name") if isinstance(properties, dict) else None
        if not isinstance(name, str):
            raise ProviderError("VWorld 응답 좌표계 정보가 올바르지 않습니다.")
        match = re.search(r"EPSG(?::|::|/)(\d{4,6})$", name, flags=re.IGNORECASE)
        supplied = f"EPSG:{match.group(1)}" if match else name
        try:
            supplied = normalize_crs(supplied)
        except CrsError as exc:
            raise ProviderError(
                f"VWorld 응답 좌표계를 확인할 수 없습니다: {self._redact(name)}"
            ) from exc
        expected = normalize_crs(expected)
        if supplied != expected:
            raise ProviderError(f"VWorld 응답 좌표계가 요청과 다릅니다: {supplied} (예상 {expected})")
        return supplied

    def _fetch_wfs_features(
        self,
        definition: LayerDefinition,
        planner: AoiTilePlanner,
        initial_tiles: list[AoiTile],
        context: _FetchContext,
    ) -> tuple[dict[str, dict[str, Any]], str]:
        pending = list(initial_tiles)
        raw_features: dict[str, dict[str, Any]] = {}
        seen_ids: dict[str, str] = {}
        response_crs = normalize_crs(definition.response_crs or definition.request_crs)
        limit = min(self._page_size, WFS_FEATURE_LIMIT)

        next_tile = 0
        while next_tile < len(pending):
            tile = pending[next_tile]
            next_tile += 1
            context.activate(tile)
            payload = self._request(
                self._endpoint(definition),
                self._params(definition, tile.request_bounds, 1),
                context,
            )
            collection = self._feature_collection(payload)
            response_crs = self._response_crs(collection, definition.response_crs or definition.request_crs)
            features = collection.get("features")
            if not isinstance(features, list):
                raise ProviderError("VWorld Feature 목록 형식이 올바르지 않습니다.")
            reported_total = self._reported_total(collection)
            saturated = len(features) >= limit or (
                reported_total is not None and reported_total > len(features)
            )
            if saturated:
                children = planner.split(tile)
                if not children:
                    raise ProviderError(
                        f"VWorld WFS 밀집 영역을 더 나눌 수 없습니다: "
                        f"layer={definition.id}, bbox={tile.output_bounds}, depth={tile.depth}, "
                        f"returned={len(features)}, total={reported_total}"
                    )
                pending.extend(children)
                context.active_tile = None
                continue

            self._validate_raw_feature_geometries(features, definition)
            self._merge_raw_features(
                raw_features,
                features,
                seen_ids=seen_ids,
            )
            context.finish_tile()

        return raw_features, response_crs

    def _fetch_data_features(
        self,
        definition: LayerDefinition,
        initial_tiles: list[AoiTile],
        context: _FetchContext,
    ) -> tuple[dict[str, dict[str, Any]], str]:
        raw_features: dict[str, dict[str, Any]] = {}
        seen_ids: dict[str, str] = {}
        response_crs = normalize_crs(definition.response_crs or definition.request_crs)

        for tile in initial_tiles:
            context.activate(tile)
            previous_fingerprint: str | None = None
            page = 1
            while True:
                payload = self._request(
                    self._endpoint(definition),
                    self._params(definition, tile.request_bounds, page),
                    context,
                )
                collection = self._feature_collection(payload)
                response_crs = self._response_crs(
                    collection,
                    definition.response_crs or definition.request_crs,
                )
                features = collection.get("features")
                if not isinstance(features, list):
                    raise ProviderError("VWorld Feature 목록 형식이 올바르지 않습니다.")
                if not features:
                    break
                page_fingerprint = self._fingerprint(features)
                if page_fingerprint == previous_fingerprint:
                    raise ProviderError("VWorld Pagination이 같은 페이지를 반복했습니다.")
                previous_fingerprint = page_fingerprint
                self._validate_raw_feature_geometries(features, definition)
                self._merge_raw_features(raw_features, features, seen_ids=seen_ids)
                if len(features) < self._page_size:
                    break
                page += 1
            context.finish_tile()
        return raw_features, response_crs

    def fetch_layer(self, definition: LayerDefinition, aoi: SpatialLayer) -> DownloadResult:
        context = _FetchContext(
            initial_tile_count=0,
            request_budget=self._request_budget_maximum,
        )

        try:
            self._endpoint(definition)
            tile_size_m = (
                self._land_ownership_tile_size_m
                if definition.id == "land_ownership"
                or definition.data_id.casefold() == "dt_d160"
                else self._default_tile_size_m
            )
            planner = AoiTilePlanner(
                aoi,
                definition.request_crs,
                tile_size_m,
                self._max_split_depth,
                self._minimum_tile_size_m,
            )
            initial_tiles = planner.initial_tiles()
            context = _FetchContext(
                initial_tile_count=len(initial_tiles),
                request_budget=compute_request_budget(
                    len(initial_tiles),
                    self._request_budget_minimum,
                    self._request_budget_per_tile,
                    self._request_budget_maximum,
                ),
            )
            if not initial_tiles:
                raise ProviderError("VWorld 조회 타일을 만들 수 없습니다.")
            if definition.service_type == "wfs" and definition.geometry_type == "Unknown":
                definition = replace(
                    definition,
                    geometry_type=self._describe_feature_type(definition, context),
                )
            if definition.service_type == "wfs":
                raw_features, response_crs = self._fetch_wfs_features(
                    definition,
                    planner,
                    initial_tiles,
                    context,
                )
            else:
                raw_features, response_crs = self._fetch_data_features(
                    definition,
                    initial_tiles,
                    context,
                )

            parsed_features: list[SpatialFeature] = []
            malformed_count = 0
            for key, raw in sorted(raw_features.items()):
                geometry_data = raw.get("geometry")
                if not isinstance(geometry_data, dict):
                    malformed_count += 1
                    continue
                try:
                    geometry = shape(geometry_data)
                except (TypeError, ValueError, ShapelyError) as exc:
                    raise ProviderError(f"VWorld Geometry를 읽을 수 없습니다: {definition.name}") from exc
                geometry = self._repair_geometry(geometry, definition.geometry_type)
                properties = raw.get("properties")
                parsed_features.append(
                    SpatialFeature(
                        geometry=geometry,
                        properties=dict(properties) if isinstance(properties, dict) else {},
                        feature_id=key,
                    )
                )
            if malformed_count:
                raise ProviderError(f"VWorld 응답에 Geometry가 없는 객체가 {malformed_count}개 있습니다.")

            request_layer = SpatialLayer(
                name=definition.id,
                display_name=definition.name,
                crs=response_crs,
                geometry_type=definition.geometry_type,
                category=definition.category,
                features=parsed_features,
                source="vworld",
            )
            output_layer = transform_layer(request_layer, aoi.crs)
            aoi_geometry = self._combined_geometry(aoi)
            output_layer.features = [
                feature for feature in output_layer.features if feature.geometry.intersects(aoi_geometry)
            ]
            return DownloadResult(
                layer_id=definition.id,
                status="success",
                feature_count=len(output_layer.features),
                layer=output_layer,
                initial_tile_count=context.initial_tile_count,
                request_count=context.request_count,
                successful_tile_count=context.successful_tile_count,
                failed_tile_count=0,
                failed_tile_bbox=None,
            )
        except (ProviderError, CrsError) as exc:
            context.capture_failure()
            return DownloadResult(
                layer_id=definition.id,
                status="failed",
                error=self._redact(str(exc)),
                initial_tile_count=context.initial_tile_count,
                request_count=context.request_count,
                successful_tile_count=context.successful_tile_count,
                failed_tile_count=1,
                failed_tile_bbox=context.failed_tile_bbox,
            )

    def fetch_many(self, definitions: list[LayerDefinition], aoi: SpatialLayer) -> list[DownloadResult]:
        results: list[DownloadResult] = []
        for definition in definitions:
            try:
                results.append(self.fetch_layer(definition, aoi))
            except ProviderError as exc:
                results.append(DownloadResult(layer_id=definition.id, status="failed", error=self._redact(str(exc))))
        return results
