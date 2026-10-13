from __future__ import annotations

from dataclasses import dataclass

import httpx

from ..errors import ProviderError
from ..gis.tiling import AoiTile
from ..models import DownloadResult, SpatialLayer


DEFAULT_MAX_RESPONSE_BYTES = 64 * 1024 * 1024


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

    def activate(self, tile: AoiTile) -> None:
        self.active_tile = tile

    def finish_tile(self) -> None:
        self.successful_tile_count += 1
        self.active_tile = None

    def before_request(self) -> None:
        if self.request_count >= self.request_budget:
            self.capture_failure()
            raise ProviderError(
                f"{self.provider} 레이어 요청 예산을 초과했습니다: budget={self.request_budget}"
            )
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
