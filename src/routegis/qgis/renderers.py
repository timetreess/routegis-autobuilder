from __future__ import annotations

from xml.etree import ElementTree as ET

from ..models import SpatialLayer
from ..styles import is_categorized, symbol_style
from .categorized_renderer import add_categorized_renderer
from .renderer_symbols import add_symbol


def add_renderer(
    parent: ET.Element,
    layer: SpatialLayer,
    *,
    shapefile: bool = False,
) -> None:
    if is_categorized(layer):
        add_categorized_renderer(parent, layer, shapefile=shapefile)
        return
    renderer = ET.SubElement(
        parent,
        "renderer-v2",
        {"type": "singleSymbol", "symbollevels": "0"},
    )
    symbols = ET.SubElement(renderer, "symbols")
    add_symbol(symbols, symbol_style(layer))
