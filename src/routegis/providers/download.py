from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from math import isfinite
from time import monotonic

import httpx

from shapely.ops import unary_union

from ..errors import ProviderError, RetryableProviderError
from ..gis.crs import transform_layer
from ..gis.tiling import AoiTile, AoiTilePlanner
from ..models import DownloadResult, SpatialLayer


DEFAULT_MAX_RESPONSE_BYTES = 64 * 1024 * 1024
MAX_RECOVERY_DEPTH = 4
TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})
# Subset of TRANSIENT_STATUS whose final failure can be recovered by splitting the tile.
RECOVERABLE_SERVER_STATUS = frozenset({500, 502, 503, 504})


@dataclass(frozen=True)
class DownloadLimits:
    """Request budget, tiling and response-size limits shared by all providers."""

    request_budget_minimum: int
    request_budget_per_tile: int
    request_budget_maximum: int
    max_split_depth: int
    minimum_tile_size_m: float
    max_response_bytes: int


def validate_download_limits(
    *,
    positive_sizes: Mapping[str, float],
    request_budget_minimum: int,
    request_budget_per_tile: int,
    request_budget_maximum: int,
    max_split_depth: int,
    minimum_tile_size_m: float,
    max_response_bytes: int,
    coerce: bool = False,
) -> DownloadLimits:
    """Validate constructor limits.

    ``positive_sizes`` holds provider-specific finite-positive values (timeout,
    tile sizes), checked first and in order; ``minimum_tile_size_m`` is always
    checked too. ``coerce`` normalises values to int/float.
    """
    for name, value in (*positive_sizes.items(), ("minimum_tile_size_m", minimum_tile_size_m)):
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
    if coerce:
        return DownloadLimits(
            int(request_budget_minimum), int(request_budget_per_tile),
            int(request_budget_maximum), int(max_split_depth),
            float(minimum_tile_size_m), int(max_response_bytes),
        )
    return DownloadLimits(
        request_budget_minimum, request_budget_per_tile, request_budget_maximum,
        max_split_depth, minimum_tile_size_m, max_response_bytes,
    )


def combined_aoi_geometry(aoi: SpatialLayer, provider: str):
    geometries = [feature.geometry for feature in aoi.features if not feature.geometry.is_empty]
    if not geometries:
        raise ProviderError(f"{provider} 조회 범위를 만들 수 없습니다.")
    return unary_union(geometries)


def filter_to_aoi(layer: SpatialLayer, aoi: SpatialLayer, provider: str) -> SpatialLayer:
    """Keep only features of *layer* (already in the AOI CRS) intersecting the AOI."""
    aoi_geometry = combined_aoi_geometry(aoi, provider)
    layer.features = [feature for feature in layer.features if feature.geometry.intersects(aoi_geometry)]
    return layer


def clip_to_aoi(request_layer: SpatialLayer, aoi: SpatialLayer, provider: str) -> SpatialLayer:
    """Transform to the AOI CRS and keep only features intersecting the AOI."""
    return filter_to_aoi(transform_layer(request_layer, aoi.crs), aoi, provider)


def is_resource_error(message: str) -> bool:
    return any(marker in message.casefold() for marker in (
        "timeout waiting for idle object", "unable to obtain connection",
        "cannot get a connection", "query timed out", "query timeout",
        "out of memory", "runtime error: query run out of memory",
    ))


def retry_delay(response: httpx.Response, fallback: float) -> float:
    """Respect a numeric Retry-After without allowing unbounded server delays."""
    try:
        seconds = float(response.headers.get("retry-after", ""))
        if isfinite(seconds) and seconds >= 0:
            return min(seconds, 60.0)
    except ValueError:
        pass
    return min(fallback, 60.0)


def split_failed_tile(planner: AoiTilePlanner, tile: AoiTile, context: FetchContext,
                      error: RetryableProviderError) -> list[AoiTile]:
    # The normal depth limit also handles saturation. Outages get a much smaller
    # limit so a wholly unavailable service does not fan out indefinitely.
    children = planner.split(tile) if tile.depth < MAX_RECOVERY_DEPTH else []
    if not children:
        raise error
    context.notify(f"일시 장애 · 실패 구간만 {len(children)}개로 분할 · depth={tile.depth + 1}, bbox={tile.output_bounds}")
    context.active_tile = None
    return children


@dataclass
class FetchContext:
    """Track a single layer's request budget and output-CRS tile statistics."""

    provider: str
    initial_tile_count: int = 0
    request_budget: int = 0
    request_count: int = 0
    successful_tile_count: int = 0
    active_tile: AoiTile | None = None
    failed_tile_bbox: tuple[float, float, float, float] | None = None
    on_progress: Callable[[str], None] | None = None
    request_budget_maximum: int | None = None
    check_cancelled: Callable[[], None] | None = None
    _last_progress_time: float = field(default=float("-inf"), init=False, repr=False)
    _last_progress_request: int = field(default=0, init=False, repr=False)

    def notify(self, message: str) -> None:
        if self.on_progress is not None:
            self.on_progress(message)

    def activate(self, tile: AoiTile) -> None:
        if self.check_cancelled is not None:
            self.check_cancelled()
        self.active_tile = tile

    def _progress(self, *, request_count: int | None = None, force: bool = False) -> None:
        count = self.request_count if request_count is None else request_count
        now = monotonic()
        if not force and now - self._last_progress_time < 5 and count - self._last_progress_request < 25:
            return
        self.notify(f"수집 진행 · API 요청 {count}/{self.request_budget}회 · 완료 타일 {self.successful_tile_count}개 · 초기 {self.initial_tile_count}개")
        self._last_progress_time = now
        self._last_progress_request = count

    def finish_tile(self) -> None:
        self.successful_tile_count += 1
        self.active_tile = None
        self._progress()

    def reserve_requests(self, count: int) -> None:
        """Reserve newly planned work, not retries, within the layer's hard cap."""
        if self.request_budget_maximum is None:
            return
        expanded = min(self.request_budget_maximum, self.request_budget + count)
        if expanded > self.request_budget:
            self.request_budget = expanded
            self.notify(f"추가 조회 작업 반영 · 레이어 요청 예산 {expanded}회 · 안전 상한 {self.request_budget_maximum}회")

    def before_request(self) -> None:
        if self.check_cancelled is not None:
            self.check_cancelled()
        if self.request_count >= self.request_budget:
            self.capture_failure()
            raise ProviderError(
                f"{self.provider} 레이어 요청 예산을 초과했습니다: budget={self.request_budget} "
                f"(실제 요청 {self.request_count}회, 완료 타일 {self.successful_tile_count}개)"
            )
        self._progress(request_count=self.request_count + 1)
        self.request_count += 1

    def capture_failure(self) -> None:
        if self.failed_tile_bbox is None and self.active_tile is not None:
            self.failed_tile_bbox = self.active_tile.output_bounds

    def success_result(self, layer: SpatialLayer) -> DownloadResult:
        self._progress(force=True)
        return DownloadResult(
            layer_id=layer.name,
            status="success",
            feature_count=len(layer.features),
            layer=layer,
            initial_tile_count=self.initial_tile_count,
            request_count=self.request_count,
            successful_tile_count=self.successful_tile_count,
        )

    def failed_result(self, layer_id: str, error: str) -> DownloadResult:
        self.capture_failure()
        self._progress(force=True)
        return DownloadResult(
            layer_id=layer_id,
            status="failed",
            error=error,
            initial_tile_count=self.initial_tile_count,
            request_count=self.request_count,
            successful_tile_count=self.successful_tile_count,
            failed_tile_count=1,
            failed_tile_bbox=self.failed_tile_bbox,
        )


def read_response_bytes(response: httpx.Response, max_bytes: int, provider: str) -> bytes:
    """Bound both declared size and decoded bytes while the caller owns the stream."""
    limit_message = f"{provider} 응답 크기 제한을 초과했습니다."
    declared_size = response.headers.get("content-length")
    if declared_size is not None:
        try:
            if int(declared_size) > max_bytes:
                raise ProviderError(limit_message)
        except ValueError:
            pass

    content = bytearray()
    for chunk in response.iter_bytes():
        if len(content) + len(chunk) > max_bytes:
            raise ProviderError(limit_message)
        content.extend(chunk)
    return bytes(content)
