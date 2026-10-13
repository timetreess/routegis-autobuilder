from __future__ import annotations

from xml.etree import ElementTree as ET

from ..models import SpatialLayer
from .categorized_renderer import _apply_native_renderer, _categorized_renderer
from .renderer_symbols import _fill_symbol


def _renderer(
    parent: ET.Element,
    layer: SpatialLayer,
    *,
    shapefile: bool = False,
) -> None:
    if layer.name == "land_ownership":
        _categorized_renderer(parent, layer, shapefile=shapefile)
        return
    renderer = ET.SubElement(
        parent,
        "renderer-v2",
        {"type": "singleSymbol", "symbollevels": "0"},
    )
    symbols = ET.SubElement(renderer, "symbols")
    symbol_type = (
        "line"
        if "Line" in layer.geometry_type
        else "fill"
        if "Polygon" in layer.geometry_type
        else "marker"
    )
    if symbol_type == "line":
        symbol = ET.SubElement(
            symbols,
            "symbol",
            {"name": "0", "type": symbol_type, "alpha": "1"},
        )
        symbol_layer = ET.SubElement(
            symbol,
            "layer",
            {"class": "SimpleLine", "enabled": "1"},
        )
        line_styles = {
            "river_centerline": ("25,118,210,255", "1.2"),
            "power_line": ("141,74,45,255", "1.2"),
        }
        color, width = line_styles.get(
            layer.name,
            (
                ("214,50,45,255", "2")
                if layer.category == "route"
                else ("76,110,143,255", "0.35")
            ),
        )
        ET.SubElement(symbol_layer, "prop", {"k": "line_color", "v": color})
        ET.SubElement(symbol_layer, "prop", {"k": "line_style", "v": "solid"})
        ET.SubElement(symbol_layer, "prop", {"k": "line_width", "v": width})
        unit = "Point" if layer.name in line_styles else "Pixel" if layer.category == "route" else "MM"
        ET.SubElement(symbol_layer, "prop", {"k": "line_width_unit", "v": unit})
        return
    if symbol_type == "fill":
        fill_styles = {
            "ecology_nature": ("76,175,80,80", "46,125,50,255", "0.8"),
            "heritage_protection": ("156,39,176,80", "106,27,154,255", "0.8"),
            "river_area": ("66,165,245,70", "25,118,210,255", "0.8"),
        }
        style = fill_styles.get(layer.name)
        if style is None and layer.name == "route_buffer":
            style = (
                "214,50,45,30",
                "214,50,45,255",
                "0.6",
            )
        if style is None and layer.category == "cadastral":
            style = (
                "255,255,255,0",
                "95,103,112,210",
                "0.35",
            )
        if style is None and layer.category == "landuse":
            style = (
                "205,181,117,90",
                "119,96,51,190",
                "0.35",
            )
        if style is None:
            style = (
                "92,140,108,70",
                "55,92,66,210",
                "0.35",
            )
        fill, outline, outline_width = style
        _fill_symbol(
            symbols,
            name="0",
            fill=fill,
            outline=outline,
            outline_width=outline_width,
            outline_width_unit="MM" if layer.name == "route_buffer" else "Point",
        )
        return
    symbol = ET.SubElement(
        symbols,
        "symbol",
        {"name": "0", "type": symbol_type, "alpha": "1"},
    )
    symbol_layer = ET.SubElement(
        symbol,
        "layer",
        {"class": "SimpleMarker", "enabled": "1"},
    )
    color, size = (
        ("141,74,45,255", "2.5")
        if layer.name == "power_support"
        else ("31,92,122,255", "2")
    )
    ET.SubElement(symbol_layer, "prop", {"k": "color", "v": color})
    ET.SubElement(symbol_layer, "prop", {"k": "name", "v": "circle"})
    ET.SubElement(symbol_layer, "prop", {"k": "size", "v": size})
