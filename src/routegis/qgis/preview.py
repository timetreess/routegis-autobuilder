from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from ..errors import ExportError


PREVIEW_GROUP_NAME = "RouteGIS 선형 후보 미리보기"
PREVIEW_PROPERTY = "RouteGIS:preview"


def add_preview_layers(paths: Mapping[str, Path]) -> tuple[tuple[str, ...], Any]:
    from qgis.core import QgsCoordinateTransform, QgsProject, QgsRectangle, QgsVectorLayer

    project = QgsProject.instance()
    root = project.layerTreeRoot()
    group = root.addGroup(PREVIEW_GROUP_NAME)
    group.setCustomProperty(PREVIEW_PROPERTY, True)
    layer_ids: list[str] = []
    combined_extent = QgsRectangle()
    try:
        for source_layer, path in paths.items():
            layer = QgsVectorLayer(str(path), f"RouteGIS 미리보기: {source_layer}", "ogr")
            if not layer.isValid():
                raise ExportError(f"QGIS에서 미리보기 레이어를 열 수 없습니다: {source_layer}")
            layer.setCustomProperty(PREVIEW_PROPERTY, True)
            project.addMapLayer(layer, False)
            group.addLayer(layer)
            layer_ids.append(layer.id())
            extent = layer.extent()
            if project.crs().isValid() and layer.crs().isValid() and layer.crs() != project.crs():
                transform = QgsCoordinateTransform(layer.crs(), project.crs(), project)
                extent = transform.transformBoundingBox(extent)
            combined_extent.combineExtentWith(extent)
    except Exception:
        remove_preview_layers(layer_ids)
        raise
    return tuple(layer_ids), combined_extent


def remove_preview_layers(layer_ids: Iterable[str]) -> None:
    from qgis.core import QgsProject

    project = QgsProject.instance()
    existing = {layer_id for layer_id in layer_ids if project.mapLayer(layer_id) is not None}
    existing.update(
        layer.id()
        for layer in project.mapLayers().values()
        if layer.customProperty(PREVIEW_PROPERTY, False)
    )
    if existing:
        project.removeMapLayers(list(existing))
    root = project.layerTreeRoot()
    for node in list(root.children()):
        if node.customProperty(PREVIEW_PROPERTY, False):
            root.removeChildNode(node)
