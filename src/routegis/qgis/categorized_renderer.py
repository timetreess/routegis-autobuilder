from __future__ import annotations

import colorsys
from importlib import import_module
from typing import NamedTuple
from xml.etree import ElementTree as ET

from ..models import SpatialLayer
from .renderer_symbols import _fill_symbol


LAND_OWNERSHIP_FIELD = "lbl"
LAND_OWNERSHIP_OPACITY = 0.7


class CategoryStyle(NamedTuple):
    value: str | None
    label: str
    fill: str
    outline: str


def _category_styles(
    layer: SpatialLayer,
    *,
    shapefile: bool,
) -> tuple[CategoryStyle, ...]:
    def canonical_value(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.rstrip(" ") if shapefile else value
        return normalized or None

    values = sorted(
        {
            canonical_value(
                None
                if feature.properties.get(LAND_OWNERSHIP_FIELD) is None
                else str(feature.properties[LAND_OWNERSHIP_FIELD])
            )
            for feature in layer.features
        },
        key=lambda value: (value is not None, (value or "").casefold()),
    )
    if not values:
        values = [None]
    styles: list[CategoryStyle] = []
    for index, value in enumerate(values):
        hue = (0.08 + index * 0.618033988749895) % 1.0
        red, green, blue = (
            round(channel * 255)
            for channel in colorsys.hsv_to_rgb(hue, 0.58, 0.88)
        )
        outline_red, outline_green, outline_blue = (
            round(channel * 0.62)
            for channel in (red, green, blue)
        )
        styles.append(
            CategoryStyle(
                value=value,
                label=(value or "").strip() or "미분류",
                fill=f"{red},{green},{blue},150",
                outline=f"{outline_red},{outline_green},{outline_blue},255",
            )
        )
    return tuple(styles)


def _categorized_renderer(
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
    for index, style in enumerate(_category_styles(layer, shapefile=shapefile)):
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
        _fill_symbol(
            symbols,
            name=symbol_name,
            fill=style.fill,
            outline=style.outline,
            outline_width="0.4",
        )


def _apply_native_renderer(
    qgis_layer,
    layer: SpatialLayer,
    *,
    shapefile: bool,
) -> bool:
    if layer.name != "land_ownership":
        return False
    qgis_core = import_module("qgis.core")
    QgsCategorizedSymbolRenderer = getattr(
        qgis_core,
        "QgsCategorizedSymbolRenderer",
    )
    QgsFillSymbol = getattr(qgis_core, "QgsFillSymbol")
    QgsRendererCategory = getattr(qgis_core, "QgsRendererCategory")

    categories = []
    for style in _category_styles(layer, shapefile=shapefile):
        symbol = QgsFillSymbol.createSimple(
            {
                "color": style.fill,
                "outline_color": style.outline,
                "outline_width": "0.4",
                "outline_width_unit": "Point",
            }
        )
        categories.append(
            QgsRendererCategory(
                style.value or "",
                symbol,
                style.label,
                True,
            )
        )
    qgis_layer.setRenderer(
        QgsCategorizedSymbolRenderer(LAND_OWNERSHIP_FIELD, categories)
    )
    qgis_layer.setOpacity(LAND_OWNERSHIP_OPACITY)
    return True
