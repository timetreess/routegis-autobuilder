from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import ezdxf
from ezdxf.entities import DXFGraphic
from ezdxf.path import make_path
from ezdxf.units import unit_name
from shapely.geometry import LineString

from ..errors import CadReadError


SUPPORTED_TYPES = {"LINE", "POLYLINE", "LWPOLYLINE", "ARC"}


@dataclass(frozen=True)
class LayerInventory:
    name: str
    entity_types: tuple[str, ...]
    entity_count: int
    total_length: float
    connected_ratio: float
    score: float = 0.0


@dataclass(frozen=True)
class SkippedCadEntity:
    entity_type: str
    handle: str
    layer: str
    reason: str


@dataclass(frozen=True)
class DxfCoordinateReport:
    bounds: tuple[float, float, float, float]
    insunits: int
    unit_name: str

    @property
    def center(self) -> tuple[float, float]:
        min_x, min_y, max_x, max_y = self.bounds
        return ((min_x + max_x) / 2, (min_y + max_y) / 2)


class _DegenerateEntityError(CadReadError):
    pass


class DxfReader:
    def __init__(self, path: Path, chord_tolerance: float = 0.1):
        self.path = Path(path)
        self.chord_tolerance = chord_tolerance
        if self.chord_tolerance <= 0:
            raise CadReadError("곡선 변환 허용오차는 0보다 커야 합니다.")
        if not self.path.is_file():
            raise CadReadError(f"DXF 파일을 찾을 수 없습니다: {self.path}")
        try:
            self.document = ezdxf.readfile(self.path)
        except (OSError, ezdxf.DXFError) as exc:
            raise CadReadError(f"DXF 도면을 열 수 없습니다: {self.path.name}") from exc
        self._line_entries: dict[str, list[tuple[str, LineString]]] | None = None
        self._skipped_entities: tuple[SkippedCadEntity, ...] = ()

    def _supported_entities(self) -> list[DXFGraphic]:
        return [entity for entity in self.document.modelspace() if entity.dxftype() in SUPPORTED_TYPES]

    def entity_to_line(self, entity: DXFGraphic) -> LineString:
        try:
            vertices = [(point.x, point.y) for point in make_path(entity).flattening(self.chord_tolerance)]
        except (TypeError, ValueError, ezdxf.DXFError) as exc:
            raise CadReadError(f"{entity.dxftype()} 객체를 선형으로 변환할 수 없습니다.") from exc
        deduplicated: list[tuple[float, float]] = []
        for vertex in vertices:
            if not deduplicated or vertex != deduplicated[-1]:
                deduplicated.append(vertex)
        if len(deduplicated) < 2:
            raise _DegenerateEntityError(f"{entity.dxftype()} 객체에 유효한 선 좌표가 없습니다.")
        line = LineString(deduplicated)
        if line.is_empty or line.length <= 0:
            raise _DegenerateEntityError(f"{entity.dxftype()} 객체의 길이가 0입니다.")
        return line

    def _scan(self) -> dict[str, list[tuple[str, LineString]]]:
        if self._line_entries is not None:
            return self._line_entries
        entries: dict[str, list[tuple[str, LineString]]] = defaultdict(list)
        skipped: list[SkippedCadEntity] = []
        for entity in self._supported_entities():
            layer = str(entity.dxf.layer)
            try:
                line = self.entity_to_line(entity)
            except _DegenerateEntityError:
                skipped.append(
                    SkippedCadEntity(
                        entity_type=entity.dxftype(),
                        handle=str(entity.dxf.handle or "?"),
                        layer=layer,
                        reason="zero_length",
                    )
                )
                continue
            entries[layer].append((entity.dxftype(), line))
        self._line_entries = dict(entries)
        self._skipped_entities = tuple(skipped)
        return self._line_entries

    @property
    def skipped_entities(self) -> tuple[SkippedCadEntity, ...]:
        self._scan()
        return self._skipped_entities

    def lines_by_layer(self) -> dict[str, list[LineString]]:
        return {
            name: [line for _entity_type, line in entries]
            for name, entries in self._scan().items()
        }

    def coordinate_report(self) -> DxfCoordinateReport | None:
        """Measure supported model-space linework, not the often stale DXF header extent."""
        bounds = [line.bounds for entries in self._scan().values() for _, line in entries]
        if not bounds:
            return None
        units = int(self.document.header.get("$INSUNITS", 0))
        return DxfCoordinateReport(
            bounds=(
                min(item[0] for item in bounds), min(item[1] for item in bounds),
                max(item[2] for item in bounds), max(item[3] for item in bounds),
            ),
            insunits=units,
            unit_name=unit_name(units),
        )

    def inventory(self, snap_tolerance: float = 0.1) -> list[LayerInventory]:
        inventory: list[LayerInventory] = []
        for name, entries in self._scan().items():
            lines = [line for _entity_type, line in entries]
            endpoint_counts: Counter[tuple[int, int]] = Counter()
            scale = 1.0 / max(snap_tolerance, 1e-12)
            for line in lines:
                for x, y in (line.coords[0], line.coords[-1]):
                    endpoint_counts[(round(x * scale), round(y * scale))] += 1
            connected_endpoints = sum(count for count in endpoint_counts.values() if count > 1)
            connected_ratio = connected_endpoints / max(len(lines) * 2, 1)
            inventory.append(
                LayerInventory(
                    name=name,
                    entity_types=tuple(sorted({entity_type for entity_type, _line in entries})),
                    entity_count=len(lines),
                    total_length=sum(line.length for line in lines),
                    connected_ratio=connected_ratio,
                )
            )
        return sorted(inventory, key=lambda item: item.name.casefold())

    def read_layer(self, layer_name: str) -> list[LineString]:
        lines = self.lines_by_layer().get(layer_name, [])
        if not lines:
            raise CadReadError(f"선형 객체가 있는 CAD 레이어를 찾을 수 없습니다: {layer_name}")
        return lines
