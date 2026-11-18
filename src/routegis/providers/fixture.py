from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from shapely.geometry import shape

from ..errors import RouteGISError
from ..models import DownloadResult, LayerDefinition, SpatialFeature, SpatialLayer


class FixtureProvider:
    """Offline provider for tests, smoke checks and release validation."""

    def __init__(self, path: Path):
        with path.open("r", encoding="utf-8") as stream:
            payload = json.load(stream)
        if not isinstance(payload, dict) or not isinstance(payload.get("layers"), dict):
            raise RouteGISError("오프라인 Fixture 형식이 올바르지 않습니다.")
        self.crs = str(payload.get("crs", "EPSG:5186"))
        self.layers: dict[str, Any] = payload["layers"]

    def fetch_layer(self, definition: LayerDefinition, aoi: SpatialLayer) -> DownloadResult:
        payload = self.layers.get(definition.id)
        if payload is None:
            return DownloadResult(definition.id, "failed", error="오프라인 Fixture에 레이어가 없습니다.")
        if isinstance(payload, dict) and payload.get("error"):
            return DownloadResult(definition.id, "failed", error=str(payload["error"]))
        features = payload.get("features") if isinstance(payload, dict) else None
        if not isinstance(features, list):
            return DownloadResult(definition.id, "failed", error="오프라인 FeatureCollection이 올바르지 않습니다.")
        parsed = []
        for index, feature in enumerate(features):
            if not isinstance(feature, dict) or not isinstance(feature.get("geometry"), dict):
                continue
            geometry = shape(feature["geometry"])
            if geometry.intersects(aoi.features[0].geometry):
                parsed.append(
                    SpatialFeature(
                        geometry,
                        dict(feature.get("properties") or {}),
                        str(feature.get("id", f"fixture-{index}")),
                    )
                )
        layer = SpatialLayer(
            name=definition.id,
            display_name=definition.name,
            crs=self.crs,
            geometry_type=definition.geometry_type,
            category=definition.category,
            features=parsed,
            source="fixture",
        )
        return DownloadResult(definition.id, "success", len(parsed), layer)

