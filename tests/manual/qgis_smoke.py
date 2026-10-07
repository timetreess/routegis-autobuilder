from __future__ import annotations

import argparse
import configparser
import importlib
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


EXPECTED_VERSION = "0.2.3"
DEFAULT_LAYER_IDS = (
    "ecology_nature",
    "land_ownership",
    "heritage_protection",
    "power_line",
    "power_support",
    "river_centerline",
    "river_area",
)
EXPECTED_GROUPS = (
    "01_노선",
    "02_생태환경",
    "03_국가유산",
    "04_수자원",
    "05_전력시설",
    "06_행정구역",
    "07_토지이용",
    "08_시설물",
    "09_원본자료",
    "10_토지",
    "11_기본도",
)
GROUP_BY_LAYER_ID = {
    "ecology_nature": "02_생태환경",
    "land_ownership": "10_토지",
    "heritage_protection": "03_국가유산",
    "power_line": "05_전력시설",
    "power_support": "05_전력시설",
    "river_centerline": "04_수자원",
    "river_area": "04_수자원",
}
QGIS_PROVIDER_PROPERTIES = {
    "routegis/provider": "provider",
    "routegis/publisher": "publisher",
    "routegis/dataset_id": "dataset_id",
    "routegis/attribution": "attribution",
    "routegis/license_name": "license_name",
    "routegis/license_url": "license_url",
}
FAKE_VWORLD_KEY = "qgis-smoke-vworld-placeholder"
FAKE_ECOLOGY_KEY = "qgis-smoke-ecology-placeholder"


class _Canvas:
    def __init__(self) -> None:
        self.extent = None
        self.refresh_count = 0

    def setExtent(self, extent: Any) -> None:
        self.extent = extent

    def refresh(self) -> None:
        self.refresh_count += 1


class _Interface:
    def __init__(self, window: Any) -> None:
        self._window = window
        self._canvas = _Canvas()
        self.toolbar_actions: list[Any] = []
        self.menu_actions: list[Any] = []

    def mainWindow(self) -> Any:
        return self._window

    def mapCanvas(self) -> _Canvas:
        return self._canvas

    def addProject(self, path: str) -> bool:
        from qgis.core import QgsProject

        return QgsProject.instance().read(path)

    def addToolBarIcon(self, action: Any) -> None:
        self.toolbar_actions.append(action)

    def addPluginToVectorMenu(self, _name: str, action: Any) -> None:
        self.menu_actions.append(action)

    def removeToolBarIcon(self, action: Any) -> None:
        self.toolbar_actions.remove(action)

    def removePluginVectorMenu(self, _name: str, action: Any) -> None:
        self.menu_actions.remove(action)


class _MessageBoxTrap:
    @staticmethod
    def critical(_parent: Any, title: str, message: str, *_args: Any) -> None:
        raise AssertionError(f"unexpected critical dialog: {title}: {message}")

    @staticmethod
    def warning(_parent: Any, title: str, message: str, *_args: Any) -> None:
        raise AssertionError(f"unexpected warning dialog: {title}: {message}")


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the RouteGIS installed-QGIS smoke test.")
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--plugin-dir", required=True, type=Path)
    return parser.parse_args()


def _plugin_version(plugin_directory: Path) -> str:
    parser = configparser.ConfigParser()
    loaded = parser.read(plugin_directory / "metadata.txt", encoding="utf-8")
    assert loaded, "plugin metadata.txt was not found"
    return parser["general"]["version"]


def _item_by_id(wizard: Any, layer_id: str) -> Any:
    Qt = getattr(importlib.import_module("qgis.PyQt.QtCore"), "Qt")

    for index in range(wizard.layer_list.count()):
        item = wizard.layer_list.item(index)
        if str(item.data(Qt.UserRole)) == layer_id:
            return item
    raise AssertionError(f"Wizard layer item not found: {layer_id}")


def _checked_ids(wizard: Any) -> tuple[str, ...]:
    return tuple(wizard._selected_layers())


def _assert_wizard_catalog(wizard: Any) -> None:
    from routegis.models import LayerDefinition

    Qt = getattr(importlib.import_module("qgis.PyQt.QtCore"), "Qt")
    QLineEdit = getattr(
        importlib.import_module("qgis.PyQt.QtWidgets"),
        "QLineEdit",
    )

    assert wizard.vworld_api_key.echoMode() == QLineEdit.Password
    assert wizard.data_go_kr_service_key.echoMode() == QLineEdit.Password
    assert _checked_ids(wizard) == DEFAULT_LAYER_IDS

    catalog_ids = {definition.id for definition in wizard.service.catalog.all()}
    legacy_ids = catalog_ids - set(DEFAULT_LAYER_IDS)
    assert legacy_ids
    assert all(_item_by_id(wizard, layer_id).checkState() == Qt.Unchecked for layer_id in legacy_ids)
    for layer_id in DEFAULT_LAYER_IDS:
        tooltip = _item_by_id(wizard, layer_id).toolTip()
        assert "공급원:" in tooltip
        assert "외부 데이터셋 ID:" in tooltip
        assert "라이선스:" in tooltip

    wizard.layer_search.setText("문화재")
    assert not _item_by_id(wizard, "heritage_protection").isHidden()
    assert _item_by_id(wizard, "power_line").isHidden()
    assert _checked_ids(wizard) == DEFAULT_LAYER_IDS

    live_definition = LayerDefinition(
        id="qgis_smoke_live_layer",
        name="QGIS Smoke 실시간 레이어",
        provider="vworld",
        service_type="wfs",
        endpoint="https://api.vworld.kr/req/wfs",
        data_id="qgis_smoke_live_layer",
        geometry_type="MultiPolygon",
        category="facility",
        request_crs="EPSG:4326",
        response_crs="EPSG:4326",
        publisher="국토교통부 공간정보 오픈플랫폼(VWorld)",
        dataset_id="qgis_smoke_live_layer",
        license_name="VWorld OpenAPI 이용조건",
        license_url="https://www.vworld.kr/dev/v4dv_wmsguide2_s001.do",
    )
    wizard._vworld_checked_ids = set(_checked_ids(wizard))
    wizard._finish_vworld_catalog(None, {"definitions": [live_definition]})
    assert _checked_ids(wizard) == DEFAULT_LAYER_IDS
    assert _item_by_id(wizard, live_definition.id).checkState() == Qt.Unchecked
    wizard.layer_search.clear()


def _assert_wizard_crs_choices(wizard: Any) -> None:
    from routegis.gis.crs import KOREAN_PROJECTED_CRS
    from qgis.core import QgsCoordinateReferenceSystem

    expected = {code for code, _name in KOREAN_PROJECTED_CRS}
    for selector in (wizard.source_crs, wizard.output_crs):
        assert {selector.itemData(index) for index in range(selector.count())} == expected
        for index in range(selector.count()):
            code = selector.itemData(index)
            assert QgsCoordinateReferenceSystem(code).isValid()
            assert selector.itemText(index).startswith(f"{code} — ")
    assert wizard._crs_code(wizard.output_crs) == wizard._crs_code(wizard.source_crs)

    wizard.source_crs.setCurrentIndex(wizard.source_crs.findData("EPSG:5174"))
    assert wizard._crs_code(wizard.output_crs) == "EPSG:5174"
    wizard.output_crs.setCurrentIndex(wizard.output_crs.findData("EPSG:5187"))
    assert wizard._crs_code(wizard.source_crs) == "EPSG:5174"
    assert wizard._crs_code(wizard.output_crs) == "EPSG:5187"
    wizard.source_crs.setCurrentIndex(wizard.source_crs.findData("EPSG:32652"))
    assert wizard._crs_code(wizard.output_crs) == "EPSG:32652"
    wizard.source_crs.setCurrentText(" epsg:5179 ")
    assert wizard._crs_code(wizard.source_crs) == "EPSG:5179"
    assert wizard._crs_code(wizard.output_crs) == "EPSG:5179"
    wizard.source_crs.setCurrentIndex(wizard.source_crs.findData("EPSG:5186"))


def _configure_fixture_build(wizard: Any, project_root: Path, output_root: Path) -> None:
    from routegis.cli import FixtureProvider

    fixture = FixtureProvider(project_root / "tests" / "fixtures" / "spatial_mock.json")
    real_build = wizard.service.build

    def fixture_build(config: Any, **kwargs: Any) -> Any:
        assert config.project_type == "non_linear"
        assert config.source_layers == ()
        assert config.source_crs == config.output_crs == "EPSG:5186"
        assert kwargs["api_key"] == FAKE_VWORLD_KEY
        assert kwargs["data_go_kr_service_key"] == FAKE_ECOLOGY_KEY
        assert kwargs["domain"] is None
        return real_build(
            config,
            api_key=kwargs["api_key"],
            data_go_kr_service_key=kwargs["data_go_kr_service_key"],
            domain=kwargs["domain"],
            provider=fixture,
        )

    wizard.service.build = fixture_build
    wizard.project_name.setText("QGIS 3.34 Smoke")
    wizard.output_directory.setText(str(output_root))
    wizard.drawing.setText(str(project_root / "tests" / "fixtures" / "sample_route.dxf"))
    wizard.source_crs.setCurrentIndex(wizard.source_crs.findData("EPSG:5186"))
    wizard.output_crs.setCurrentIndex(wizard.output_crs.findData("EPSG:5186"))
    wizard.buffer_distance.setValue(1000)
    wizard._analyze()
    assert wizard.project_type == "non_linear"
    assert wizard.candidate_table.rowCount() > 0
    wizard.vworld_api_key.setText(FAKE_VWORLD_KEY)
    wizard.data_go_kr_service_key.setText(FAKE_ECOLOGY_KEY)


def _assert_color(color: Any, expected: tuple[int, int, int, int]) -> None:
    assert color.getRgb() == expected


def _assert_native_styles(project: Any) -> None:
    qgis_core = importlib.import_module("qgis.core")
    QgsCategorizedSymbolRenderer = getattr(
        qgis_core,
        "QgsCategorizedSymbolRenderer",
    )
    QgsFillSymbol = getattr(qgis_core, "QgsFillSymbol")
    QgsLineSymbol = getattr(qgis_core, "QgsLineSymbol")
    QgsMarkerSymbol = getattr(qgis_core, "QgsMarkerSymbol")
    QgsUnitTypes = getattr(qgis_core, "QgsUnitTypes")

    fill_styles = {
        "ecology_nature": ((76, 175, 80, 80), (46, 125, 50, 255), 0.8),
        "heritage_protection": ((156, 39, 176, 80), (106, 27, 154, 255), 0.8),
        "river_area": ((66, 165, 245, 70), (25, 118, 210, 255), 0.8),
    }
    line_styles = {
        "river_centerline": ((25, 118, 210, 255), 1.2),
        "power_line": ((141, 74, 45, 255), 1.2),
    }
    marker_styles = {"power_support": ((141, 74, 45, 255), 2.5)}
    from routegis.providers import LayerCatalog

    catalog = LayerCatalog()
    for layer_id, style in fill_styles.items():
        definition = catalog.get(layer_id)
        layers = project.mapLayersByName(definition.name)
        assert len(layers) == 1
        symbol = layers[0].renderer().symbol()
        assert symbol is not None
        assert isinstance(symbol, QgsFillSymbol)
        symbol_layer = symbol.symbolLayer(0)
        _assert_color(symbol_layer.fillColor(), style[0])
        _assert_color(symbol_layer.strokeColor(), style[1])
        assert abs(symbol_layer.strokeWidth() - style[2]) < 1e-9
        assert symbol_layer.strokeWidthUnit() == QgsUnitTypes.RenderPoints
    for layer_id, style in line_styles.items():
        definition = catalog.get(layer_id)
        symbol = project.mapLayersByName(definition.name)[0].renderer().symbol()
        assert isinstance(symbol, QgsLineSymbol)
        _assert_color(symbol.color(), style[0])
        assert abs(symbol.width() - style[1]) < 1e-9
        assert symbol.symbolLayer(0).widthUnit() == QgsUnitTypes.RenderPoints
    for layer_id, style in marker_styles.items():
        definition = catalog.get(layer_id)
        symbol = project.mapLayersByName(definition.name)[0].renderer().symbol()
        assert isinstance(symbol, QgsMarkerSymbol)
        _assert_color(symbol.color(), style[0])
        assert abs(symbol.size() - style[1]) < 1e-9
        assert symbol.outputUnit() == QgsUnitTypes.RenderMillimeters

    ownership = project.mapLayersByName(catalog.get("land_ownership").name)
    assert len(ownership) == 1
    assert abs(ownership[0].opacity() - 0.7) < 1e-9
    renderer = ownership[0].renderer()
    assert isinstance(renderer, QgsCategorizedSymbolRenderer)
    assert renderer.classAttribute() == "lbl"
    assert [category.value() for category in renderer.categories()] == ["개인"]


def _assert_native_category_edge_values(temporary_root: Path) -> None:
    from routegis.gis.gpkg import write_geopackage
    from routegis.gis.shapefile import write_shapefile
    from routegis.models import SpatialFeature, SpatialLayer
    from routegis.qgis.renderers import _apply_native_renderer
    from shapely.geometry import Polygon

    QgsVectorLayer = getattr(
        importlib.import_module("qgis.core"),
        "QgsVectorLayer",
    )
    QgsRenderContext = getattr(
        importlib.import_module("qgis.core"),
        "QgsRenderContext",
    )
    geometry = Polygon(
        [(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)]
    )
    layer_spec = SpatialLayer(
        "land_ownership",
        "토지소유공간정보 경계값",
        "EPSG:5186",
        "Polygon",
        "land",
        [
            SpatialFeature(geometry, {"lbl": "개인 "}, "edge-1"),
            SpatialFeature(geometry, {"lbl": None}, "edge-2"),
        ],
    )
    def assert_distinct_rendered_colors(layer: Any, *, shapefile: bool) -> None:
        assert layer.isValid()
        assert _apply_native_renderer(layer, layer_spec, shapefile=shapefile)
        renderer = layer.renderer()
        context = QgsRenderContext()
        colors: list[tuple[int, int, int, int]] = []
        renderer.startRender(context, layer.fields())
        try:
            rendered_symbols = [
                renderer.symbolForFeature(feature, context)
                for feature in layer.getFeatures()
            ]
            assert all(symbol is not None for symbol in rendered_symbols)
            colors = [symbol.color().getRgb() for symbol in rendered_symbols]
        finally:
            renderer.stopRender(context)
        assert len(colors) == len(set(colors)) == 2

    shapefile_path = temporary_root / "category-edge.shp"
    write_shapefile(shapefile_path, layer_spec)
    assert_distinct_rendered_colors(
        QgsVectorLayer(str(shapefile_path), layer_spec.display_name, "ogr"),
        shapefile=True,
    )

    geopackage = write_geopackage(
        temporary_root / "category-edge.gpkg",
        [layer_spec],
    )
    assert_distinct_rendered_colors(
        QgsVectorLayer(
            f"{geopackage}|layername={layer_spec.name}",
            layer_spec.display_name,
            "ogr",
        ),
        shapefile=False,
    )


def _assert_loaded_project(project: Any, wizard: Any) -> None:
    from routegis.providers import LayerCatalog

    QgsVectorLayer = getattr(
        importlib.import_module("qgis.core"),
        "QgsVectorLayer",
    )

    root = project.layerTreeRoot()
    assert tuple(child.name() for child in root.children()) == EXPECTED_GROUPS
    basemap = root.findGroup("11_기본도")
    assert basemap is not None
    assert [child.name() for child in basemap.children()] == ["VWorld Street", "VWorld Hybrid"]

    assert wizard.result is not None
    for layer in project.mapLayers().values():
        if not isinstance(layer, QgsVectorLayer) or layer.name() == "원본 CAD":
            continue
        source = layer.source().split("|", 1)[0]
        stored_path = Path(project.pathResolver().readPath(source))
        assert stored_path.is_file()
        assert stored_path.suffix.lower() == ".shp"
        assert wizard.result.project_directory in stored_path.parents
        assert layer.dataProvider().encoding().upper() in {
            "EUC-KR",
            "CP949",
            "WINDOWS-949",
        }

    catalog = LayerCatalog()
    provider_layer_ids: set[str] = set()
    for layer_id in DEFAULT_LAYER_IDS:
        definition = catalog.get(layer_id)
        layers = project.mapLayersByName(definition.name)
        assert len(layers) == 1
        layer = layers[0]
        provider_layer_ids.add(layer.id())
        tree_layer = root.findLayer(layer.id())
        assert tree_layer is not None
        assert tree_layer.parent().name() == GROUP_BY_LAYER_ID[layer_id]
        expected_attribution = (
            "© OpenStreetMap contributors"
            if definition.provider == "osm"
            else definition.publisher
        )
        expected_metadata = {
            "provider": definition.provider,
            "publisher": definition.publisher,
            "dataset_id": definition.dataset_id,
            "attribution": expected_attribution,
            "license_name": definition.license_name,
            "license_url": definition.license_url,
        }
        for property_name, metadata_name in QGIS_PROVIDER_PROPERTIES.items():
            assert layer.customProperty(property_name, "") == expected_metadata[metadata_name]

    for layer in project.mapLayers().values():
        if layer.id() in provider_layer_ids:
            continue
        assert all(layer.customProperty(name, "") == "" for name in QGIS_PROVIDER_PROPERTIES)

    _assert_native_styles(project)
    assert wizard.vworld_api_key.text() == FAKE_VWORLD_KEY
    assert wizard.data_go_kr_service_key.text() == FAKE_ECOLOGY_KEY


def _assert_no_placeholder_leak(result: Any) -> None:
    artifacts = (
        result.manifest,
        result.report_html,
        result.project_directory / "reports" / "processing_report.json",
        result.project_directory / "logs" / "app.log",
    )
    for artifact in artifacts:
        text = artifact.read_text(encoding="utf-8")
        assert FAKE_VWORLD_KEY not in text
        assert FAKE_ECOLOGY_KEY not in text


def _assert_headless_project_layout(project_root: Path, temporary_root: Path) -> None:
    from unittest.mock import patch

    from routegis.app.project_service import ProjectService
    from routegis.cli import FixtureProvider
    from routegis.models import ProjectConfig
    from routegis.providers import LayerCatalog
    from qgis.core import QgsProject

    with patch("routegis.qgis.project._native_project", return_value=False):
        result = ProjectService().build(
            ProjectConfig(
                project_name="Headless layout smoke",
                source_drawing=project_root / "tests" / "fixtures" / "sample_route.dxf",
                source_layers=(), source_crs="EPSG:5186", output_crs="EPSG:5186",
                buffer_distance=1000, selected_layers=DEFAULT_LAYER_IDS,
                output_directory=temporary_root / "headless", project_type="non_linear",
            ),
            provider=FixtureProvider(project_root / "tests" / "fixtures" / "spatial_mock.json"),
        )

    project = QgsProject.instance()
    assert project.read(str(result.project_file))
    root = project.layerTreeRoot()
    assert tuple(child.name() for child in root.children()) == EXPECTED_GROUPS
    catalog = LayerCatalog()
    ownership_name = catalog.get("land_ownership").name
    ownership = project.mapLayersByName(ownership_name)
    assert len(ownership) == 1 and ownership[0].isValid()
    assert abs(ownership[0].opacity() - 0.7) < 1e-9
    assert root.findLayer(ownership[0].id()).parent().name() == "10_토지"
    order = [layer.name() for layer in root.layerOrder()]
    assert order.index(ownership_name) < order.index("VWorld Street")
    assert all(
        order.index(catalog.get(layer_id).name) < order.index(ownership_name)
        for layer_id in DEFAULT_LAYER_IDS if layer_id != "land_ownership"
    )


def _assert_empty_result_group_labels(project_root: Path, temporary_root: Path) -> None:
    from contextlib import nullcontext
    from unittest.mock import patch

    from routegis.app.project_service import ProjectService
    from routegis.cli import FixtureProvider
    from routegis.models import ProjectConfig
    from routegis.providers import LayerCatalog
    from qgis.core import QgsProject

    empty_ids = {"land_ownership", "river_centerline", "river_area", "power_line"}

    class EmptyFixture(FixtureProvider):
        def fetch_layer(self, definition, aoi):
            result = super().fetch_layer(definition, aoi)
            if definition.id in empty_ids:
                assert result.layer is not None
                result.layer.features.clear()
                result.feature_count = 0
            return result

    expected = tuple(
        f"{name} (해당없음)" if name in {"04_수자원", "10_토지"} else name
        for name in EXPECTED_GROUPS
    )
    catalog = LayerCatalog()
    for native in (True, False):
        context = nullcontext() if native else patch("routegis.qgis.project._native_project", return_value=False)
        with context:
            result = ProjectService().build(
                ProjectConfig(
                    project_name=f"Empty groups {native}",
                    source_drawing=project_root / "tests" / "fixtures" / "sample_route.dxf",
                    source_layers=(), source_crs="EPSG:5186", output_crs="EPSG:5186",
                    buffer_distance=1000, selected_layers=DEFAULT_LAYER_IDS,
                    output_directory=temporary_root / "empty-groups", project_type="non_linear",
                ),
                provider=EmptyFixture(project_root / "tests" / "fixtures" / "spatial_mock.json"),
            )
        project = QgsProject.instance()
        assert project.read(str(result.project_file))
        root = project.layerTreeRoot()
        assert tuple(child.name() for child in root.children()) == expected
        for layer_id in DEFAULT_LAYER_IDS:
            layers = project.mapLayersByName(catalog.get(layer_id).name)
            assert len(layers) == 1 and layers[0].isValid()
            assert (layers[0].featureCount() == 0) == (layer_id in empty_ids)
        assert not root.findGroup("06_행정구역").children()


def run(project_root: Path, plugin_directory: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    project_root = project_root.resolve()
    plugin_directory = plugin_directory.resolve()
    assert (project_root / "tests" / "fixtures" / "spatial_mock.json").is_file()
    assert (plugin_directory / "__init__.py").is_file()

    QSettings = getattr(
        importlib.import_module("qgis.PyQt.QtCore"),
        "QSettings",
    )
    QMainWindow = getattr(
        importlib.import_module("qgis.PyQt.QtWidgets"),
        "QMainWindow",
    )
    qgis_core = importlib.import_module("qgis.core")
    Qgis = getattr(qgis_core, "Qgis")
    QgsApplication = getattr(qgis_core, "QgsApplication")
    QgsProject = getattr(qgis_core, "QgsProject")

    prefix = os.environ.get("QGIS_PREFIX_PATH")
    if prefix:
        QgsApplication.setPrefixPath(prefix, True)

    application: Any = None
    plugin: Any = None
    with tempfile.TemporaryDirectory(prefix="routegis-qgis-smoke-") as temporary:
        temporary_root = Path(temporary)
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(temporary_root / "settings"))
        application = QgsApplication([], False)
        application.initQgis()
        try:
            assert Qgis.QGIS_VERSION.startswith("3.34.3")
            sys.path.insert(0, str(plugin_directory.parent))
            plugin_module = importlib.import_module(plugin_directory.name)
            import routegis
            from routegis.providers.ecology import EcologyClient
            from routegis.providers.overpass import OverpassClient
            from routegis.providers.vworld import VWorldClient

            assert routegis.__version__ == EXPECTED_VERSION
            assert _plugin_version(plugin_directory) == EXPECTED_VERSION
            assert all((EcologyClient, OverpassClient, VWorldClient))

            wizard_module = importlib.import_module(f"{plugin_directory.name}.wizard")
            routegis_plugin_module = importlib.import_module(
                f"{plugin_directory.name}.routegis_plugin"
            )
            setattr(wizard_module, "QMessageBox", _MessageBoxTrap)
            setattr(routegis_plugin_module, "QMessageBox", _MessageBoxTrap)
            setattr(wizard_module, "load_vworld_api_key", lambda *_paths: "")
            setattr(
                wizard_module,
                "load_data_go_kr_service_key",
                lambda *_paths: "",
            )

            interface = _Interface(QMainWindow())
            plugin = plugin_module.classFactory(interface)
            plugin.initGui()
            assert len(interface.toolbar_actions) == len(interface.menu_actions) == 1
            assert not plugin.action.icon().isNull()
            plugin.run()
            application.processEvents()
            wizard = plugin.wizard
            assert wizard is not None and wizard.isVisible()
            _assert_wizard_catalog(wizard)
            _assert_wizard_crs_choices(wizard)

            _configure_fixture_build(wizard, project_root, temporary_root / "output")
            wizard._build()
            application.processEvents()
            assert wizard.result is not None
            assert [result.layer_id for result in wizard.result.downloads] == list(DEFAULT_LAYER_IDS)
            assert all(result.status == "success" for result in wizard.result.downloads)
            assert wizard.result.project_file.is_file()
            _assert_no_placeholder_leak(wizard.result)
            _assert_loaded_project(QgsProject.instance(), wizard)
            _assert_native_category_edge_values(temporary_root)
            _assert_headless_project_layout(project_root, temporary_root)
            _assert_empty_result_group_labels(project_root, temporary_root)

            wizard.close()
            application.processEvents()
            plugin.run()
            application.processEvents()
            reopened_wizard = plugin.wizard
            assert reopened_wizard is not None and reopened_wizard.isVisible()
            assert reopened_wizard.vworld_api_key.text() == FAKE_VWORLD_KEY
            assert reopened_wizard.data_go_kr_service_key.text() == FAKE_ECOLOGY_KEY
        finally:
            if plugin is not None:
                plugin.unload()
            QgsProject.instance().clear()
            if application is not None:
                application.processEvents()
            QgsApplication.exitQgis()


def main() -> int:
    args = _arguments()
    try:
        run(args.project_root, args.plugin_dir)
    except Exception as exc:
        print(f"QGIS smoke failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"QGIS 3.34.3 RouteGIS {EXPECTED_VERSION} smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
