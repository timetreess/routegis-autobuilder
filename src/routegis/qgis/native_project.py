from __future__ import annotations

from collections.abc import Mapping
from importlib import import_module
from pathlib import Path

from ..config import resource_root
from ..errors import ExportError
from ..models import SpatialLayer
from .project_schema import (
    VWORLD_HYBRID_GROUP_NAME,
    VWORLD_HYBRID_LAYERS,
    VWORLD_STREET_NAME,
    VWORLD_STREET_URI,
    _group_for,
    _group_labels,
    _provider_properties,
    _style_name,
    _vector_source,
)
from .renderers import _apply_native_renderer


def _native_project(
    path: Path,
    project_name: str,
    geopackage: Path,
    layers: list[SpatialLayer],
    source_drawing: Path,
    source_crs: str,
    layer_metadata: Mapping[str, Mapping[str, str]] | None = None,
) -> bool:
    layer_metadata = layer_metadata or {}
    try:
        qgis_core = import_module("qgis.core")
    except ModuleNotFoundError:
        return False
    Qgis = getattr(qgis_core, "Qgis")
    QgsCoordinateReferenceSystem = getattr(
        qgis_core,
        "QgsCoordinateReferenceSystem",
    )
    QgsProject = getattr(qgis_core, "QgsProject")
    QgsRasterLayer = getattr(qgis_core, "QgsRasterLayer")
    QgsRectangle = getattr(qgis_core, "QgsRectangle")
    QgsReferencedRectangle = getattr(qgis_core, "QgsReferencedRectangle")
    QgsVectorLayer = getattr(qgis_core, "QgsVectorLayer")

    project = QgsProject()
    project.setTitle(project_name)
    project.setCrs(QgsCoordinateReferenceSystem(layers[0].crs))
    project.setFilePathStorage(Qgis.FilePathType.Relative)
    root = project.layerTreeRoot()
    groups = {name: root.addGroup(label) for name, label in _group_labels(layers).items()}
    project_directory = path.parent
    for layer_spec in layers:
        source = _vector_source(project_directory, geopackage, layer_spec)
        layer = QgsVectorLayer(source.absolute_uri, layer_spec.display_name, "ogr")
        if not layer.isValid():
            raise ExportError(
                f"QGIS에서 레이어를 열 수 없습니다: {layer_spec.display_name}"
            )
        project.addMapLayer(layer, False)
        groups[_group_for(layer_spec)].addLayer(layer)
        for name, value in _provider_properties(
            layer_spec.name,
            layer_metadata,
        ).items():
            layer.setCustomProperty(name, value)
        if not _apply_native_renderer(
            layer,
            layer_spec,
            shapefile=source.encoding == "EUC-KR",
        ):
            style_name = _style_name(layer_spec)
            style_path = resource_root() / "styles" / f"{style_name}.qml"
            if style_path.is_file():
                layer.loadNamedStyle(str(style_path))
    source_layer = QgsVectorLayer(str(source_drawing), "원본 CAD", "ogr")
    if source_layer.isValid():
        source_layer.setCrs(QgsCoordinateReferenceSystem(source_crs))
        project.addMapLayer(source_layer, False)
        groups["09_원본자료"].addLayer(source_layer)
        groups["09_원본자료"].setItemVisibilityChecked(False)
    vworld_layer = QgsRasterLayer(VWORLD_STREET_URI, VWORLD_STREET_NAME, "wms")
    if not vworld_layer.isValid():
        raise ExportError("QGIS에서 VWorld Street 기본도를 열 수 없습니다.")
    project.addMapLayer(vworld_layer, False)
    groups["11_기본도"].addLayer(vworld_layer).setItemVisibilityChecked(True)
    hybrid_group = groups["11_기본도"].addGroup(VWORLD_HYBRID_GROUP_NAME)
    hybrid_group.setItemVisibilityChecked(True)
    hybrid_group.setExpanded(True)
    for layer_name, uri in VWORLD_HYBRID_LAYERS:
        layer = QgsRasterLayer(uri, layer_name, "wms")
        if not layer.isValid():
            raise ExportError(f"QGIS에서 {layer_name} 기본도를 열 수 없습니다.")
        project.addMapLayer(layer, False)
        hybrid_group.addLayer(layer).setItemVisibilityChecked(True)
    route_spec = next(
        (layer for layer in layers if layer.name == "linework"),
        layers[0],
    )
    if route_spec.bounds is not None:
        min_x, min_y, max_x, max_y = route_spec.bounds
        rectangle = QgsReferencedRectangle(
            QgsRectangle(min_x, min_y, max_x, max_y),
            QgsCoordinateReferenceSystem(route_spec.crs),
        )
        project.viewSettings().setDefaultViewExtent(rectangle)
    return bool(project.write(str(path)))
