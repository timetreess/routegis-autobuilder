from __future__ import annotations

from dataclasses import replace

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

