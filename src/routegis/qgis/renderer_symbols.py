from __future__ import annotations

from xml.etree import ElementTree as ET


def _fill_symbol(
    parent: ET.Element,
    *,
    name: str,
    fill: str,
    outline: str,
    outline_width: str,
) -> None:
    symbol = ET.SubElement(
        parent,
        "symbol",
        {"name": name, "type": "fill", "alpha": "1"},
    )
    symbol_layer = ET.SubElement(
        symbol,
        "layer",
        {"class": "SimpleFill", "enabled": "1"},
    )
    ET.SubElement(symbol_layer, "prop", {"k": "color", "v": fill})
    ET.SubElement(symbol_layer, "prop", {"k": "outline_color", "v": outline})
    ET.SubElement(
        symbol_layer,
        "prop",
        {"k": "outline_width", "v": outline_width},
    )
    ET.SubElement(
        symbol_layer,
        "prop",
        {"k": "outline_width_unit", "v": "Point"},
    )
