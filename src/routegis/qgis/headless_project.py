from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from xml.etree import ElementTree as ET

from ..models import SpatialLayer
from .project_schema import (
    VWORLD_HYBRID_GROUP_NAME,
    VWORLD_HYBRID_LAYERS,
    VWORLD_STREET_NAME,
    VWORLD_STREET_URI,
    _group_for,
    _group_labels,
    _layer_id,
    _project_relative_path,
    _provider_properties,
    _srs_element,
    _vector_source,
)
from .renderers import _renderer


def _headless_qgs(
    project_name: str,
    project_directory: Path,
    geopackage: Path,
    layers: list[SpatialLayer],
    source_drawing: Path,
    source_crs: str,
    version: str,
    layer_metadata: Mapping[str, Mapping[str, str]] | None = None,
) -> bytes:
    layer_metadata = layer_metadata or {}
    root = ET.Element(
        "qgis",
        {"projectname": project_name, "version": f"{version}-RouteGIS"},
    )
    ET.SubElement(root, "homePath", {"path": ""})
    ET.SubElement(root, "title").text = project_name
    project_crs = ET.SubElement(root, "projectCrs")
    _srs_element(project_crs, layers[0].crs)

    tree_root = ET.SubElement(root, "layer-tree-group")
    group_nodes = {
        name: ET.SubElement(
            tree_root,
            "layer-tree-group",
            {"name": label, "checked": "Qt::Checked"},
        )
        for name, label in _group_labels(layers).items()
    }
    project_layers = ET.SubElement(root, "projectlayers")
    layer_order = ET.SubElement(root, "layerorder")
    for layer in layers:
        identifier = _layer_id(layer.name)
        source = _vector_source(project_directory, geopackage, layer)
        ET.SubElement(
            group_nodes[_group_for(layer)],
            "layer-tree-layer",
            {
                "id": identifier,
                "name": layer.display_name,
                "source": source.relative_uri,
                "providerKey": "ogr",
                "checked": "Qt::Checked",
                "expanded": "1",
            },
        )
        map_layer = ET.SubElement(
            project_layers,
            "maplayer",
            {
                "type": "vector",
                "geometry": (
                    "Line"
                    if "Line" in layer.geometry_type
                    else "Polygon"
                    if "Polygon" in layer.geometry_type
                    else "Point"
                ),
                "wkbType": layer.geometry_type,
                "readOnly": "0",
                "styleCategories": "AllStyleCategories",
            },
        )
        ET.SubElement(map_layer, "id").text = identifier
        ET.SubElement(map_layer, "datasource").text = source.relative_uri
        ET.SubElement(map_layer, "layername").text = layer.display_name
        srs = ET.SubElement(map_layer, "srs")
        _srs_element(srs, layer.crs)
        ET.SubElement(map_layer, "provider", {"encoding": source.encoding}).text = "ogr"
        provider_properties = _provider_properties(layer.name, layer_metadata)
        if provider_properties:
            custom_properties = ET.SubElement(map_layer, "customproperties")
            options = ET.SubElement(custom_properties, "Option", {"type": "Map"})
            for name, value in provider_properties.items():
                ET.SubElement(
                    options,
                    "Option",
                    {"name": name, "type": "QString", "value": value},
                )
        _renderer(map_layer, layer, shapefile=source.encoding == "EUC-KR")

    source_id = _layer_id("source_cad")
    source_uri = _project_relative_path(
        project_directory,
        source_drawing,
        label="원본 도면",
    )
    ET.SubElement(
        group_nodes["09_원본자료"],
        "layer-tree-layer",
        {
            "id": source_id,
            "name": "원본 CAD",
            "source": source_uri,
            "providerKey": "ogr",
            "checked": "Qt::Unchecked",
        },
    )
    source_layer = ET.SubElement(
        project_layers,
        "maplayer",
        {
            "type": "vector",
            "geometry": "Unknown",
            "wkbType": "Unknown",
            "readOnly": "1",
            "styleCategories": "AllStyleCategories",
        },
    )
    ET.SubElement(source_layer, "id").text = source_id
    ET.SubElement(source_layer, "datasource").text = source_uri
    ET.SubElement(source_layer, "layername").text = "원본 CAD"
    source_srs = ET.SubElement(source_layer, "srs")
    _srs_element(source_srs, source_crs)
    ET.SubElement(source_layer, "provider", {"encoding": "UTF-8"}).text = "ogr"

    vworld_id = _layer_id(VWORLD_STREET_NAME)
    ET.SubElement(
        group_nodes["11_기본도"],
        "layer-tree-layer",
        {
            "id": vworld_id,
            "name": VWORLD_STREET_NAME,
            "source": VWORLD_STREET_URI,
            "providerKey": "wms",
            "checked": "Qt::Checked",
        },
    )
    vworld_layer = ET.SubElement(project_layers, "maplayer", {"type": "raster"})
    ET.SubElement(vworld_layer, "id").text = vworld_id
    ET.SubElement(vworld_layer, "datasource").text = VWORLD_STREET_URI
    ET.SubElement(vworld_layer, "layername").text = VWORLD_STREET_NAME
    vworld_srs = ET.SubElement(vworld_layer, "srs")
    _srs_element(vworld_srs, "EPSG:3857")
    ET.SubElement(
        vworld_layer,
        "provider",
        {"encoding": "UTF-8", "mapUnitsMm": "0"},
    ).text = "wms"

    hybrid_group = ET.SubElement(
        group_nodes["11_기본도"],
        "layer-tree-group",
        {
            "name": VWORLD_HYBRID_GROUP_NAME,
            "checked": "Qt::Checked",
            "expanded": "1",
        },
    )
    for layer_name, uri in VWORLD_HYBRID_LAYERS:
        identifier = _layer_id(layer_name)
        ET.SubElement(
            hybrid_group,
            "layer-tree-layer",
            {
                "id": identifier,
                "name": layer_name,
                "source": uri,
                "providerKey": "wms",
                "checked": "Qt::Checked",
            },
        )
        map_layer = ET.SubElement(project_layers, "maplayer", {"type": "raster"})
        ET.SubElement(map_layer, "id").text = identifier
        ET.SubElement(map_layer, "datasource").text = uri
        ET.SubElement(map_layer, "layername").text = layer_name
        srs = ET.SubElement(map_layer, "srs")
        _srs_element(srs, "EPSG:3857")
        ET.SubElement(
            map_layer,
            "provider",
            {"encoding": "UTF-8", "mapUnitsMm": "0"},
        ).text = "wms"

    for tree_layer in tree_root.iter("layer-tree-layer"):
        ET.SubElement(layer_order, "layer", {"id": tree_layer.attrib["id"]})

    route = next((layer for layer in layers if layer.name == "linework"), layers[0])
    bounds = route.bounds or layers[0].bounds or (0.0, 0.0, 1.0, 1.0)
    min_x, min_y, max_x, max_y = bounds
    margin_x = max((max_x - min_x) * 0.05, 1.0)
    margin_y = max((max_y - min_y) * 0.05, 1.0)
    canvas = ET.SubElement(
        root,
        "mapcanvas",
        {"name": "theMapCanvas", "annotationsVisible": "1"},
    )
    ET.SubElement(canvas, "units").text = "meters"
    extent = ET.SubElement(canvas, "extent")
    for tag, value in (
        ("xmin", min_x - margin_x),
        ("ymin", min_y - margin_y),
        ("xmax", max_x + margin_x),
        ("ymax", max_y + margin_y),
    ):
        ET.SubElement(extent, tag).text = format(value, ".15g")
    destination = ET.SubElement(canvas, "destinationsrs")
    _srs_element(destination, route.crs)
    properties = ET.SubElement(root, "properties")
    # QGIS discards projectCrs when this legacy reprojection flag is absent.
    spatial_ref = ET.SubElement(properties, "SpatialRefSys")
    ET.SubElement(spatial_ref, "ProjectionsEnabled", {"type": "int"}).text = "1"
    paths = ET.SubElement(properties, "Paths")
    ET.SubElement(paths, "Absolute", {"type": "bool"}).text = "false"
    measurement = ET.SubElement(properties, "Measurement")
    ET.SubElement(measurement, "DistanceUnits", {"type": "QString"}).text = "meters"
    ET.SubElement(measurement, "AreaUnits", {"type": "QString"}).text = "m2"
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
