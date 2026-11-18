from __future__ import annotations

from xml.etree import ElementTree as ET

from ..models import SpatialLayer
from ..styles import (
    LAND_OWNERSHIP_FIELD,
    LAND_OWNERSHIP_OPACITY,
    category_styles,
)
from .renderer_symbols import add_symbol


def add_categorized_renderer(
    parent: ET.Element,
    layer: SpatialLayer,
    *,
    shapefile: bool,
) -> None:
    ET.SubElement(parent, "layerOpacity").text = str(LAND_OWNERSHIP_OPACITY)
    renderer = ET.SubElement(
        parent,
        "renderer-v2",
        {
            "type": "categorizedSymbol",
            "symbollevels": "0",
            "forceraster": "0",
            "enableorderby": "0",
            "attr": LAND_OWNERSHIP_FIELD,
        },
    )
    categories = ET.SubElement(renderer, "categories")
    symbols = ET.SubElement(renderer, "symbols")
    for index, style in enumerate(category_styles(layer, shapefile=shapefile)):
        symbol_name = str(index)
        ET.SubElement(
            categories,
            "category",
            {
                "value": style.value or "",
                "label": style.label,
                "symbol": symbol_name,
                "type": "string",
                "render": "true",
            },
        )
        add_symbol(symbols, style.symbol, name=symbol_name)
