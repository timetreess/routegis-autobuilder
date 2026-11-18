from __future__ import annotations

from xml.etree import ElementTree as ET

from ..styles import SymbolStyle

SYMBOL_CLASSES = {"line": "SimpleLine", "fill": "SimpleFill", "marker": "SimpleMarker"}


def number(value: float) -> str:
    return format(value, "g")


def symbol_properties(symbol: SymbolStyle) -> list[tuple[str, str]]:
    """QGIS simple-symbol properties for a neutral SymbolStyle."""
    if symbol.kind == "line":
        return [
            ("line_color", symbol.color),
            ("line_style", "solid"),
            ("line_width", number(symbol.width)),
            ("line_width_unit", symbol.width_unit),
        ]
    if symbol.kind == "fill":
        return [
            ("color", symbol.color),
            ("outline_color", symbol.outline),
            ("outline_width", number(symbol.width)),
            ("outline_width_unit", symbol.width_unit),
        ]
    return [
        ("color", symbol.color),
        ("name", "circle"),
        ("size", number(symbol.size)),
    ]


def add_symbol(parent: ET.Element, symbol: SymbolStyle, *, name: str = "0") -> None:
    element = ET.SubElement(
        parent,
        "symbol",
        {"name": name, "type": symbol.kind, "alpha": "1"},
    )
    symbol_layer = ET.SubElement(
        element,
        "layer",
        {"class": SYMBOL_CLASSES[symbol.kind], "enabled": "1"},
    )
    for key, value in symbol_properties(symbol):
        ET.SubElement(symbol_layer, "prop", {"k": key, "v": value})
