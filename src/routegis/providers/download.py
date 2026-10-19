from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
from math import isfinite

import httpx

from ..errors import ProviderError, RetryableProviderError
from ..gis.tiling import AoiTile, AoiTilePlanner
from ..models import DownloadResult, SpatialLayer


DEFAULT_MAX_RESPONSE_BYTES = 64 * 1024 * 1024
MAX_RECOVERY_DEPTH = 4


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

    def notify(self, message: str) -> None:
        if self.on_progress is not None:
            self.on_progress(message)

    def activate(self, tile: AoiTile) -> None:
        self.active_tile = tile
        self.notify(f"타일 처리 중 · 완료 {self.successful_tile_count}개 · 초기 {self.initial_tile_count}개 · depth={tile.depth}")

    def finish_tile(self) -> None:
        self.successful_tile_count += 1
        self.active_tile = None
        self.notify(f"타일 완료 {self.successful_tile_count}개 · 실제 API 요청 {self.request_count}회")

    def reserve_requests(self, count: int) -> None:
        """Reserve newly planned work, not retries, within the layer's hard cap."""
        if self.request_budget_maximum is None:
            return
        expanded = min(self.request_budget_maximum, self.request_budget + count)
        if expanded > self.request_budget:
            self.request_budget = expanded
            self.notify(f"추가 조회 작업 반영 · 레이어 요청 예산 {expanded}회 · 안전 상한 {self.request_budget_maximum}회")

    def before_request(self) -> None:
        if self.request_count >= self.request_budget:
            self.capture_failure()
            raise ProviderError(
                f"{self.provider} 레이어 요청 예산을 초과했습니다: budget={self.request_budget} "
                f"(실제 요청 {self.request_count}회, 완료 타일 {self.successful_tile_count}개)"
            )
        self.notify(f"API 요청 {self.request_count + 1}/{self.request_budget}회 · 완료 타일 {self.successful_tile_count}개")
        self.request_count += 1

    def capture_failure(self) -> None:
        if self.failed_tile_bbox is None and self.active_tile is not None:
            self.failed_tile_bbox = self.active_tile.output_bounds

    def success_result(self, layer: SpatialLayer) -> DownloadResult:
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
