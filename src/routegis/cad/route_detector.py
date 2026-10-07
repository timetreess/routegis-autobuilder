from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import replace
from math import hypot

from shapely.geometry import LineString

from ..errors import RouteAssemblyError
from .reader import LayerInventory


ROUTE_KEYWORDS = (
    "CENTERLINE",
    "ROAD_CENTER",
    "ALIGNMENT",
    "CENTER",
    "ALIGN",
    "도로중심선",
    "중심선",
    "계획선",
    "노선",
    "CL",
)


def rank_candidates(inventory: list[LayerInventory]) -> list[LayerInventory]:
    max_length = max((item.total_length for item in inventory), default=1.0)
    ranked: list[LayerInventory] = []
    for item in inventory:
        name_upper = item.name.upper()
        keyword_score = max(
            (40.0 - index * 2 for index, keyword in enumerate(ROUTE_KEYWORDS) if keyword in name_upper),
            default=0.0,
        )
        linear_score = 15.0 if set(item.entity_types) <= {"LINE", "POLYLINE", "LWPOLYLINE", "ARC"} else 0.0
        length_score = 25.0 * item.total_length / max_length
        continuity_score = 15.0 * item.connected_ratio
        count_score = min(item.entity_count, 10) * 0.5
        ranked.append(
            replace(
                item,
                score=round(keyword_score + linear_score + length_score + continuity_score + count_score, 3),
            )
        )
    return sorted(ranked, key=lambda item: (-item.score, -item.total_length, item.name.casefold()))


class _UnionFind:
    def __init__(self, size: int):
        self.parent = list(range(size))

    def find(self, item: int) -> int:
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


def assemble_route(lines: list[LineString], tolerance: float) -> LineString:
    if tolerance <= 0:
        raise RouteAssemblyError("노선 연결 허용오차는 0보다 커야 합니다.")
    if not lines:
        raise RouteAssemblyError("선택한 레이어에 노선 객체가 없습니다.")
    if any(line.is_empty or len(line.coords) < 2 for line in lines):
        raise RouteAssemblyError("비어 있거나 좌표가 부족한 노선 객체가 있습니다.")

    endpoints = [coordinate for line in lines for coordinate in (line.coords[0], line.coords[-1])]
    union_find = _UnionFind(len(endpoints))
    for left in range(len(endpoints)):
        for right in range(left + 1, len(endpoints)):
            if hypot(endpoints[left][0] - endpoints[right][0], endpoints[left][1] - endpoints[right][1]) <= tolerance:
                union_find.union(left, right)

    clusters: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for index, coordinate in enumerate(endpoints):
        clusters[union_find.find(index)].append(coordinate)
    cluster_centers = {
        root: (
            sum(point[0] for point in points) / len(points),
            sum(point[1] for point in points) / len(points),
        )
        for root, points in clusters.items()
    }

    segment_nodes: list[tuple[int, int]] = []
    segment_coords: list[list[tuple[float, float]]] = []
    graph: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for segment_index, line in enumerate(lines):
        start_root = union_find.find(segment_index * 2)
        end_root = union_find.find(segment_index * 2 + 1)
        coordinates = list(line.coords)
        coordinates[0] = cluster_centers[start_root]
        coordinates[-1] = cluster_centers[end_root]
        segment_nodes.append((start_root, end_root))
        segment_coords.append(coordinates)
        graph[start_root].append((segment_index, end_root))
        graph[end_root].append((segment_index, start_root))

    if any(len(edges) > 2 for edges in graph.values()):
        raise RouteAssemblyError("선택한 노선에 분기점이 있습니다. 하나의 중심선만 선택해 주세요.")

    visited_nodes: set[int] = set()
    queue = deque([next(iter(graph))])
    while queue:
        node = queue.popleft()
        if node in visited_nodes:
            continue
        visited_nodes.add(node)
        queue.extend(neighbor for _, neighbor in graph[node])
    if len(visited_nodes) != len(graph):
        raise RouteAssemblyError("허용오차 내에서 연결되지 않은 노선 조각이 있습니다.")

    ends = sorted(node for node, edges in graph.items() if len(edges) == 1)
    current = ends[0] if ends else min(graph)
    used_segments: set[int] = set()
    ordered: list[tuple[float, float]] = []
    while len(used_segments) < len(lines):
        candidates = sorted(edge for edge in graph[current] if edge[0] not in used_segments)
        if not candidates:
            raise RouteAssemblyError("노선 조각의 연결 순서를 결정할 수 없습니다.")
        segment_index, next_node = candidates[0]
        used_segments.add(segment_index)
        start_node, end_node = segment_nodes[segment_index]
        coordinates = segment_coords[segment_index]
        if current == end_node and current != start_node:
            coordinates = list(reversed(coordinates))
        if ordered:
            ordered.extend(coordinates[1:])
        else:
            ordered.extend(coordinates)
        current = next_node

    route = LineString(ordered)
    if route.is_empty or route.length <= 0 or not route.is_simple:
        raise RouteAssemblyError("노선이 비어 있거나 자기 교차하여 사용할 수 없습니다.")
    return route
