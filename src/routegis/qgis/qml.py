"""Standalone ``.qml`` style files generated from the shared style data."""
from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

from ..styles import STYLES
from .renderer_symbols import SYMBOL_CLASSES, symbol_properties


def qml_text(key: str) -> str:
    symbol = STYLES[key]
    root = ET.Element("qgis", {"version": "3.40.0", "styleCategories": "Symbology"})
    renderer = ET.SubElement(root, "renderer-v2", {"type": "singleSymbol"})
    symbols = ET.SubElement(renderer, "symbols")
    element = ET.SubElement(symbols, "symbol", {"name": "0", "type": symbol.kind})
    layer = ET.SubElement(element, "layer", {"class": SYMBOL_CLASSES[symbol.kind]})
    options = ET.SubElement(layer, "Option", {"type": "Map"})
    for name, value in symbol_properties(symbol):
        ET.SubElement(
            options,
            "Option",
            {"name": name, "value": value, "type": "QString"},
        )
    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    return "<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>\n" + body + "\n"


def write_qml_files(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for key in STYLES:
        if not key.startswith("default_"):
            (directory / f"{key}.qml").write_text(qml_text(key), encoding="utf-8")
