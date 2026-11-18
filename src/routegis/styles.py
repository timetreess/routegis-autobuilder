"""Layer styles defined once as neutral data.

The QGIS renderer XML, the generated ``.qml`` files and the Leaflet web map are
all derived from the tables in this module. Colours are ``"r,g,b,a"`` strings
(0-255); widths use the QGIS units ``"MM"``, ``"Point"`` or ``"Pixel"``.
"""
from __future__ import annotations

import colorsys
from typing import NamedTuple

from .models import SpatialLayer


class SymbolStyle(NamedTuple):
    kind: str  # "line", "fill" or "marker"
    color: str  # line colour, fill colour or marker colour
    outline: str = ""  # fill outline colour
    width: float = 0.35  # line width or fill outline width
    width_unit: str = "MM"
    size: float = 2.0  # marker size in mm


class CategoryStyle(NamedTuple):
    value: str | None
    label: str
    symbol: SymbolStyle


STYLES: dict[str, SymbolStyle] = {
    "route": SymbolStyle("line", "214,50,45,255", width=2, width_unit="Pixel"),
    "river_line": SymbolStyle("line", "25,118,210,255", width=1.2, width_unit="Point"),
    "power_line": SymbolStyle("line", "141,74,45,255", width=1.2, width_unit="Point"),
    "default_line": SymbolStyle("line", "76,110,143,255"),
    "ecology": SymbolStyle("fill", "76,175,80,80", "46,125,50,255", 0.8, "Point"),
    "heritage": SymbolStyle("fill", "156,39,176,80", "106,27,154,255", 0.8, "Point"),
    "river_area": SymbolStyle("fill", "66,165,245,70", "25,118,210,255", 0.8, "Point"),
    "buffer": SymbolStyle("fill", "214,50,45,30", "214,50,45,255", 0.6, "MM"),
    "cadastral": SymbolStyle("fill", "255,255,255,0", "95,103,112,210", 0.35, "Point"),
    "landuse": SymbolStyle("fill", "205,181,117,90", "119,96,51,190", 0.35, "Point"),
    "default_fill": SymbolStyle("fill", "92,140,108,70", "55,92,66,210", 0.35, "Point"),
    "power_support": SymbolStyle("marker", "141,74,45,255", size=2.5),
    "default_marker": SymbolStyle("marker", "31,92,122,255"),
}

# Layer id -> style key; layer categories are matched afterwards (see style_key).
STYLE_NAMES = {
    "ecology_nature": "ecology",
    "heritage_protection": "heritage",
    "river_centerline": "river_line",
    "river_area": "river_area",
    "power_line": "power_line",
    "power_support": "power_support",
    "route_buffer": "buffer",
}
CATEGORY_STYLES = {"route": "route", "cadastral": "cadastral", "landuse": "landuse"}

LAND_OWNERSHIP_LAYER = "land_ownership"
LAND_OWNERSHIP_FIELD = "lbl"
LAND_OWNERSHIP_OPACITY = 0.7
LAND_OWNERSHIP_OUTLINE_WIDTH = 0.4


def geometry_kind(layer: SpatialLayer) -> str:
    if "Line" in layer.geometry_type:
        return "line"
    return "fill" if "Polygon" in layer.geometry_type else "marker"


def style_key(layer: SpatialLayer) -> str:
    kind = geometry_kind(layer)
    for key in (STYLE_NAMES.get(layer.name), CATEGORY_STYLES.get(layer.category)):
        if key is not None and STYLES[key].kind == kind:
            return key
    return f"default_{kind}"


def symbol_style(layer: SpatialLayer) -> SymbolStyle:
    return STYLES[style_key(layer)]


def is_categorized(layer: SpatialLayer) -> bool:
    return layer.name == LAND_OWNERSHIP_LAYER


def layer_opacity(layer: SpatialLayer) -> float:
    return LAND_OWNERSHIP_OPACITY if is_categorized(layer) else 1.0


def category_styles(
    layer: SpatialLayer,
    *,
    shapefile: bool = False,
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
        outline = ",".join(str(round(channel * 0.62)) for channel in (red, green, blue))
        styles.append(
            CategoryStyle(
                value=value,
                label=(value or "").strip() or "미분류",
                symbol=SymbolStyle(
                    "fill",
                    f"{red},{green},{blue},150",
                    f"{outline},255",
                    LAND_OWNERSHIP_OUTLINE_WIDTH,
                    "Point",
                ),
            )
        )
    return tuple(styles)


def leaflet_style(symbol: SymbolStyle, opacity: float = 1.0) -> dict:
    """Leaflet path options for one symbol (CSS pixels at 96 dpi)."""

    def color(value: str) -> tuple[str, float]:
        red, green, blue, alpha = map(int, value.split(","))
        return f"#{red:02x}{green:02x}{blue:02x}", alpha / 255 * opacity

    stroke, alpha = color(symbol.outline if symbol.kind == "fill" else symbol.color)
    factor = {"MM": 96 / 25.4, "Point": 96 / 72, "Pixel": 1}.get(symbol.width_unit, 1)
    result = {
        "color": stroke,
        "opacity": alpha,
        "weight": symbol.width * factor,
        "fill": symbol.kind != "line",
    }
    if symbol.kind != "line":
        result["fillColor"], result["fillOpacity"] = color(symbol.color)
    if symbol.kind == "marker":
        result["radius"] = symbol.size * 96 / 25.4 / 2
    return result
