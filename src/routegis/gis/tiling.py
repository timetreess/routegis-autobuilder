from __future__ import annotations

from dataclasses import dataclass
from math import ceil, isfinite

from shapely.geometry import box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from ..models import SpatialLayer
from .crs import normalize_crs, transform_bounds, validate_projected_metre_crs


@dataclass(frozen=True)
class AoiTile:
    output_bounds: tuple[float, float, float, float]
    request_bounds: tuple[float, float, float, float]
    depth: int = 0


class AoiTilePlanner:
    def __init__(
        self,
        aoi: SpatialLayer,
        request_crs: str,
        tile_size_m: float,
        max_split_depth: int,
        minimum_tile_size_m: float,
    ):
        if not isfinite(tile_size_m):
            raise ValueError("tile_size_m must be finite")
        if tile_size_m <= 0:
            raise ValueError("tile_size_m must be positive")
        if max_split_depth < 0:
            raise ValueError("max_split_depth must be non-negative")
        if not isfinite(minimum_tile_size_m):
            raise ValueError("minimum_tile_size_m must be finite")
        if minimum_tile_size_m <= 0:
            raise ValueError("minimum_tile_size_m must be positive")

        validate_projected_metre_crs(aoi.crs)
        output_crs = normalize_crs(aoi.crs)
        normalized_request_crs = normalize_crs(request_crs)
        geometries = [feature.geometry for feature in aoi.features if not feature.geometry.is_empty]
        if not geometries:
            raise ValueError("AOI must contain at least one non-empty geometry")

        self._aoi_geometry: BaseGeometry = unary_union(geometries)
        self._output_crs = output_crs
        self._request_crs = normalized_request_crs
        self._tile_size_m = tile_size_m
        self._max_split_depth = max_split_depth
        self._minimum_tile_size_m = minimum_tile_size_m

    def initial_tiles(self) -> list[AoiTile]:
        min_x, min_y, max_x, max_y = self._aoi_geometry.bounds
        column_count = ceil((max_x - min_x) / self._tile_size_m)
        row_count = ceil((max_y - min_y) / self._tile_size_m)
        tiles: list[AoiTile] = []
        for row in range(row_count):
            lower_y = min_y + row * self._tile_size_m
            upper_y = min(lower_y + self._tile_size_m, max_y)
            for column in range(column_count):
                lower_x = min_x + column * self._tile_size_m
                upper_x = min(lower_x + self._tile_size_m, max_x)
                output_bounds = (lower_x, lower_y, upper_x, upper_y)
                if box(*output_bounds).intersects(self._aoi_geometry):
                    tiles.append(self._tile(output_bounds, depth=0))
        return tiles

    def split(self, tile: AoiTile) -> list[AoiTile]:
        if tile.depth >= self._max_split_depth:
            return []

        min_x, min_y, max_x, max_y = tile.output_bounds
        width = max_x - min_x
        height = max_y - min_y
        if width >= height:
            half_axis = width / 2
            if height < self._minimum_tile_size_m or half_axis < self._minimum_tile_size_m:
                return []
            midpoint = min_x + half_axis
            child_bounds = [
                (min_x, min_y, midpoint, max_y),
                (midpoint, min_y, max_x, max_y),
            ]
        else:
            half_axis = height / 2
            if width < self._minimum_tile_size_m or half_axis < self._minimum_tile_size_m:
                return []
            midpoint = min_y + half_axis
            child_bounds = [
                (min_x, min_y, max_x, midpoint),
                (min_x, midpoint, max_x, max_y),
            ]

        return [
            self._tile(bounds, depth=tile.depth + 1)
            for bounds in child_bounds
            if box(*bounds).intersects(self._aoi_geometry)
        ]

    def _tile(self, output_bounds: tuple[float, float, float, float], depth: int) -> AoiTile:
        request_bounds = transform_bounds(output_bounds, self._output_crs, self._request_crs)
        return AoiTile(output_bounds=output_bounds, request_bounds=request_bounds, depth=depth)


def compute_request_budget(initial_count: int, minimum: int, per_tile: int, maximum: int) -> int:
    if initial_count < 0:
        raise ValueError("initial_count must be non-negative")
    if minimum < 0:
        raise ValueError("minimum must be non-negative")
    if per_tile < 0:
        raise ValueError("per_tile must be non-negative")
    if maximum <= 0:
        raise ValueError("maximum must be positive")
    return min(maximum, max(minimum, initial_count * per_tile))
