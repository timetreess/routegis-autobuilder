"""Self-contained, read-only web maps; no QGIS or network calls during export."""
from __future__ import annotations

import base64
import html
import json
import re
import tempfile
from collections.abc import Callable, Mapping, Sequence
from functools import lru_cache
from pathlib import Path

from pyproj import Transformer
from pyproj.exceptions import ProjError
from shapely.errors import ShapelyError
from shapely.geometry import mapping
from shapely.ops import transform

from .errors import ExportError
from .gis.crs import transform_bounds
from .models import DownloadResult, SpatialLayer
from .qgis.project_schema import group_for
from .styles import (
    LAND_OWNERSHIP_FIELD,
    category_styles,
    is_categorized,
    layer_opacity,
    leaflet_style,
    symbol_style,
)
from .security import redact_sensitive_text


WEB_RESOURCES = Path(__file__).parent / "resources" / "web"


def _layer_style(layer: SpatialLayer) -> dict:
    opacity = layer_opacity(layer)
    if not is_categorized(layer):
        return {"default": leaflet_style(symbol_style(layer), opacity), "field": None, "categories": []}
    categories = [
        {"value": item.value or "", "label": item.label, "style": leaflet_style(item.symbol, opacity)}
        for item in category_styles(layer)
    ]
    return {"default": categories[0]["style"], "field": LAND_OWNERSHIP_FIELD, "categories": categories}


def _script_json(value: object) -> str:
    # HTML parsers recognize </script> even inside application/json scripts.
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            .replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def _page_template() -> tuple[str, str]:
    leaflet = WEB_RESOURCES / "leaflet"
    css = (leaflet / "leaflet.css").read_text(encoding="utf-8")

    def image(match):
        content = (leaflet / match.group(1)).read_bytes()
        return "url(data:image/png;base64," + base64.b64encode(content).decode("ascii") + ")"

    css = re.sub(r"url\(['\"]?(images/[^)'\"\s]+)['\"]?\)", image, css)
    library = (leaflet / "leaflet.js").read_text(encoding="utf-8")
    library = re.sub(r"//# sourceMappingURL=.*", "", library)
    document = (WEB_RESOURCES / "map.html").read_text(encoding="utf-8")
    for token, content in (
        ("__LEAFLET_CSS__", css), ("__LEAFLET_JS__", library),
        ("__VIEWER_CSS__", (WEB_RESOURCES / "map.css").read_text(encoding="utf-8")),
        ("__VIEWER_JS__", (WEB_RESOURCES / "map.js").read_text(encoding="utf-8")),
        ("__LEAFLET_LICENSE__", html.escape((leaflet / "LICENSE").read_text(encoding="utf-8"))),
    ):
        document = document.replace(token, content)
    return tuple(document.split("<!-- ROUTEGIS_PAYLOAD -->"))


def write_web_map(
    path: Path, project_name: str, layers: Sequence[SpatialLayer], *,
    layer_metadata: Mapping[str, Mapping[str, str]] | None = None,
    downloads: Sequence[DownloadResult] = (),
    on_progress: Callable[[int, str], None] | None = None,
    secrets: tuple[str, ...] = (),
) -> Path:
    """Embed vector data, styles, Leaflet, CSS and licenses in one portable HTML.

    Background tiles are optional online images. Raw/source CAD is never embedded.
    The caller's existing output remains untouched on failure or cancellation.
    """
    notify = on_progress or (lambda percent, message: None)
    notify(0, "단일 HTML 웹 지도 준비 중")
    metadata = layer_metadata or {}
    results = {item.layer_id: item for item in downloads}
    vectors = [layer for layer in layers if layer.category != "source" and
               (layer.name not in results or results[layer.name].status == "success")]

    @lru_cache(maxsize=4096)
    def clean_text(text: str) -> str:
        return redact_sensitive_text(text, *secrets) or ""

    def clean(value):
        if isinstance(value, str):
            return clean_text(value)
        if isinstance(value, Mapping):
            return {clean_text(str(key)): clean(item) for key, item in value.items()}
        if isinstance(value, (tuple, list)):
            return [clean(item) for item in value]
        return value

    entries = [{
        "id": layer.name, "name": layer.display_name, "group": group_for(layer),
        "count": len(layer.features), "status": "success", "error": None,
        "metadata": dict(metadata.get(layer.name, {})), "style": _layer_style(layer),
        "data": f"routegis-data-{index}",
        "order": 100 if layer.name == "linework" else 0 if layer.name == "route_buffer" else
                 10 if layer.name == "land_ownership" else 60 if "Point" in layer.geometry_type else
                 50 if "Line" in layer.geometry_type else 20,
    } for index, layer in enumerate(vectors)]
    for result in downloads:
        if result.status == "success":
            continue
        info = dict(metadata.get(result.layer_id, {}))
        entries.append({"id": result.layer_id, "name": info.get("name", result.layer_id),
                        "group": "수집 실패", "count": 0, "status": "failed",
                        "error": result.error, "metadata": info, "data": None})
    extent_layer = next((layer for layer in vectors if layer.name == "route_buffer"), None)
    if extent_layer is None:
        extent_layer = next((layer for layer in vectors if layer.features), None)
    bounds = (transform_bounds(extent_layer.bounds, extent_layer.crs, "EPSG:4326")
              if extent_layer is not None and extent_layer.bounds else (124, 33, 132, 39))
    config = clean({"title": project_name, "bounds": bounds, "layers": entries})
    total = sum(len(layer.features) for layer in vectors)
    done = 0
    temporary = None
    path = Path(path)
    try:
        prefix, suffix = _page_template()
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
            output.write(prefix)
            output.write('<script id="routegis-config" type="application/json">' + _script_json(config) + '</script>\n')
            for index, layer in enumerate(vectors):
                converter = Transformer.from_crs(layer.crs, "EPSG:4326", always_xy=True)
                output.write(f'<script id="routegis-data-{index}" type="application/json">{{"type":"FeatureCollection","features":[')
                for feature_index, feature in enumerate(layer.features):
                    geometry = transform(converter.transform, feature.geometry)
                    item = {"type": "Feature", "geometry": mapping(geometry), "properties": clean(feature.properties)}
                    if feature.feature_id is not None:
                        item["id"] = clean_text(str(feature.feature_id))
                    if feature_index:
                        output.write(",")
                    output.write(_script_json(item))
                    done += 1
                    if done % 250 == 0 or feature_index + 1 == len(layer.features):
                        notify(done * 100 // max(total, 1), f"{clean_text(layer.display_name)} 웹 지도 변환 중 · {done:,}/{total:,}개")
                output.write("]}</script>\n")
            output.write(suffix)
        notify(100, f"단일 HTML 웹 지도 생성 완료 · {done:,}개 객체")
        temporary.replace(path)
        return path
    except (OSError, ValueError, TypeError, ShapelyError, ProjError) as exc:
        raise ExportError(f"HTML 웹 지도를 저장할 수 없습니다: {clean_text(str(exc))}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
