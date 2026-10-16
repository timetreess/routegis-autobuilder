from __future__ import annotations

from math import isfinite

from ..errors import ExportError
from ..gis.crs import validate_projected_metre_crs
from .project_schema import (
    VWORLD_HYBRID_GROUP_NAME,
    VWORLD_HYBRID_LAYERS,
    VWORLD_STREET_NAME,
    VWORLD_STREET_URI,
)


class CadMapPreview:
    """Own a temporary map, separate from the user's open QGIS project."""

    def __init__(self, canvas):
        from qgis.core import QgsCoordinateReferenceSystem, QgsProject, QgsRasterLayer

        self.canvas = canvas
        self.project = QgsProject()
        self.project.setCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
        self.canvas.setProject(self.project)
        self.canvas.setDestinationCrs(self.project.crs())
        self.line_layer = None
        self._extent = None
        self._basemap = "street"
        self._basemaps = {}
        root = self.project.layerTreeRoot()
        self._drawing_group = root.addGroup("09_원본자료")
        group = root.addGroup("11_기본도")
        hybrid_group = group.addGroup(VWORLD_HYBRID_GROUP_NAME)
        for key, parent, definitions in (
            ("street", group, ((VWORLD_STREET_NAME, VWORLD_STREET_URI),)),
            ("hybrid", hybrid_group, VWORLD_HYBRID_LAYERS),
        ):
            self._basemaps[key] = []
            for name, uri in definitions:
                layer = QgsRasterLayer(uri, name, "wms")
                self.project.addMapLayer(layer, False)
                parent.addLayer(layer)
                self._basemaps[key].append(layer)

    def load_lines(self, lines_by_layer):
        for _ in self.iter_load_lines(lines_by_layer):
            pass

    def iter_load_lines(self, lines_by_layer, batch_size=500):
        """Add bounded batches on the GUI thread, yielding between event turns."""
        from qgis.PyQt.QtCore import QVariant
        from qgis.core import QgsFeature, QgsField, QgsGeometry, QgsLineSymbol, QgsVectorLayer

        self.clear_drawing()
        layer = QgsVectorLayer("LineString", "DXF 선 객체 미리보기", "memory")
        provider = layer.dataProvider()
        provider.addAttributes([QgsField("dxf_layer", QVariant.String)])
        layer.updateFields()
        total = sum(map(len, lines_by_layer.values()))
        if not total:
            raise ExportError("DXF 선 객체 미리보기를 만들 수 없습니다.")
        self.project.addMapLayer(layer, False)
        self._drawing_group.addLayer(layer)
        self.line_layer = layer
        features = []
        done = 0
        for name, lines in lines_by_layer.items():
            for line in lines:
                geometry = QgsGeometry()
                geometry.fromWkb(line.wkb)
                feature = QgsFeature(layer.fields())
                feature.setGeometry(geometry)
                feature.setAttributes([name])
                features.append(feature)
                if len(features) >= batch_size:
                    success, _ = provider.addFeatures(features)
                    if not success:
                        raise ExportError("DXF 선 객체 미리보기를 만들 수 없습니다.")
                    done += len(features)
                    features = []
                    yield done, total
        if features:
            success, _ = provider.addFeatures(features)
            if not success:
                raise ExportError("DXF 선 객체 미리보기를 만들 수 없습니다.")
            done += len(features)
            yield done, total
        layer.updateExtents()
        layer.renderer().setSymbol(QgsLineSymbol.createSimple({
            "line_color": "235,35,65,255", "line_width": "0.6",
        }))

    def set_source_crs(self, code: str) -> tuple[float, float]:
        from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform

        self.canvas.stopRendering()
        self.canvas.setLayers([])
        self._extent = None
        validate_projected_metre_crs(code)
        source = QgsCoordinateReferenceSystem(code)
        if not source.isValid() or self.line_layer is None:
            raise ExportError("미리보기 원본 좌표계를 확인해 주세요.")
        # Reinterpret the unchanged CAD X/Y values, rather than transform them twice.
        self.line_layer.setCrs(source)
        transform = QgsCoordinateTransform(source, self.project.crs(), self.project)
        extent = transform.transformBoundingBox(self.line_layer.extent())
        if not all(isfinite(value) for value in (
            extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum(),
        )) or max(abs(extent.yMinimum()), abs(extent.yMaximum())) > 20037509:
            raise ExportError("선택한 좌표계로 기본도에 표시할 수 없는 범위입니다.")
        center = QgsCoordinateTransform(
            source, QgsCoordinateReferenceSystem("EPSG:4326"), self.project,
        ).transform(self.line_layer.extent().center())
        extent.grow(max(extent.width(), extent.height(), 100) * 0.1)
        self._extent = extent
        self.set_basemap(self._basemap)
        self.zoom_to_drawing()
        return center.x(), center.y()

    def set_basemap(self, name: str):
        self._basemap = name
        if self._extent is not None:
            self.canvas.setLayers([self.line_layer, *self._basemaps[name]])
            self.canvas.refresh()

    def zoom_to_drawing(self):
        if self._extent is not None:
            self.canvas.setExtent(self._extent)
            self.canvas.refresh()

    def clear_drawing(self):
        self.canvas.stopRendering()
        self.canvas.setLayers([])
        self._extent = None
        if self.line_layer is not None:
            self.project.removeMapLayer(self.line_layer.id())
            self.line_layer = None
        self.canvas.refresh()

    def close(self):
        self.clear_drawing()
        self._basemaps.clear()
        self.project.clear()
