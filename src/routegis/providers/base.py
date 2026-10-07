from __future__ import annotations

from typing import Protocol

from ..models import DownloadResult, LayerDefinition, SpatialLayer


class SpatialDataProvider(Protocol):
    def fetch_layer(self, definition: LayerDefinition, aoi: SpatialLayer) -> DownloadResult: ...
