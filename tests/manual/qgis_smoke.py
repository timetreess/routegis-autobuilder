from __future__ import annotations

import argparse
import configparser
import importlib
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any


EXPECTED_VERSION = "0.2.9"
CORE_PACKAGE = ""
DEFAULT_LAYER_IDS = (
    "ecology_nature",
    "land_ownership",
    "heritage_protection",
    "power_line",
    "power_support",
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


def _core_module(name: str = "") -> Any:
    # Use QGIS's import hook so test-only core modules also unload on reload.
    return __import__(f"{CORE_PACKAGE}.{name}" if name else CORE_PACKAGE, fromlist=["*"])


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
    parser.add_argument("--previous-plugin-dir", type=Path, help="Test an in-process upgrade from this extracted older plugin.")
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
    from unittest.mock import patch
    from qgis.core import QgsApplication

    LayerDefinition = _core_module("models").LayerDefinition

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
    _item_by_id(wizard, "river_centerline").setCheckState(Qt.Checked)
    assert _checked_ids(wizard) == (*DEFAULT_LAYER_IDS, "river_centerline")
    _item_by_id(wizard, "river_centerline").setCheckState(Qt.Unchecked)
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
    wizard.vworld_api_key.setText(FAKE_VWORLD_KEY)
    # Hold the task in the queue while the user edits the still-enabled list.
    with patch.object(QgsApplication, "taskManager") as task_manager:
        wizard.load_vworld_button.click()
        task_manager.return_value.addTask.assert_called_once()
        assert wizard.layer_list.isEnabled()
        assert not wizard.load_vworld_button.isEnabled()
        _item_by_id(wizard, "heritage_protection").setCheckState(Qt.Unchecked)
        _item_by_id(wizard, "power_line").setCheckState(Qt.Unchecked)
        _item_by_id(wizard, "cadastral").setCheckState(Qt.Checked)
        selected = _checked_ids(wizard)
        wizard._finish_vworld_catalog(None, {"definitions": [live_definition]})
    assert _checked_ids(wizard) == selected
    assert f"선택 {len(selected)}개" in wizard.layer_status.text()
    assert wizard.load_vworld_button.isEnabled()
    assert _item_by_id(wizard, live_definition.id).checkState() == Qt.Unchecked
    wizard._render_layer_list(set(DEFAULT_LAYER_IDS))
    wizard.layer_search.clear()


def _assert_wizard_crs_choices(wizard: Any) -> None:
    KOREAN_PROJECTED_CRS = _core_module("gis.crs").KOREAN_PROJECTED_CRS
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


def _configure_fixture_build(wizard: Any, project_root: Path, output_root: Path, provider=None) -> None:
    FixtureProvider = _core_module("cli").FixtureProvider

    fixture = provider or FixtureProvider(project_root / "tests" / "fixtures" / "spatial_mock.json")
    real_build = wizard.service.build

    def fixture_build(config: Any, **kwargs: Any) -> Any:
        assert config.project_type == "non_linear"
        assert config.source_layers == ()
        assert config.source_crs == config.output_crs == "EPSG:5186"
        assert kwargs["api_key"] == FAKE_VWORLD_KEY
        assert kwargs["data_go_kr_service_key"] == FAKE_ECOLOGY_KEY
        assert kwargs["domain"] is None
        assert kwargs["use_native_project"] is False
        return real_build(config, provider=fixture, **kwargs)

    wizard.service.build = fixture_build
    wizard.project_name.setText("QGIS 3.34 Smoke")
    wizard.output_directory.setText(str(output_root))
    wizard.business_region.setCurrentText("경기도")
    wizard.preview_canvas.setRenderFlag(False)  # XYZ tiles require live networking.
    wizard.drawing.setText(str(project_root / "tests" / "fixtures" / "sample_route.dxf"))
    wizard.source_crs.setCurrentIndex(wizard.source_crs.findData("EPSG:5186"))
    wizard.output_crs.setCurrentIndex(wizard.output_crs.findData("EPSG:5186"))
    wizard.buffer_distance.setValue(1000)
    wizard._analyze()
    _wait_for_analysis(wizard)
    assert wizard.project_type == "non_linear"
    assert wizard.candidate_table.rowCount() > 0
    wizard.vworld_api_key.setText(FAKE_VWORLD_KEY)
    wizard.data_go_kr_service_key.setText(FAKE_ECOLOGY_KEY)


def _wait_for_analysis(wizard, timeout=60):
    from time import monotonic, sleep
    from qgis.PyQt.QtWidgets import QApplication

    deadline = monotonic() + timeout
    while (wizard._analysis_task is not None or wizard._preview_iterator is not None) and monotonic() < deadline:
        QApplication.processEvents()
        sleep(0.005)
    assert wizard._analysis_task is None and wizard._preview_iterator is None, "Drawing analysis timed out"


def _assert_responsive_analysis(wizard_module, interface, temporary_root):
    import ezdxf
    from threading import Event
    from time import monotonic
    from unittest.mock import patch
    from qgis.PyQt.QtCore import QTimer
    from qgis.PyQt.QtWidgets import QWizard

    drawing = temporary_root / "large-80km.dxf"
    document = ezdxf.new()
    document.units = 6
    for index in range(20_000):
        document.modelspace().add_line((200000 + index * 4, 500000), (200004 + index * 4, 500000))
    document.saveas(drawing)
    service_class = _core_module("app.project_service").ProjectService
    real_analyze = service_class.analyze_drawing
    for cancel in (False, True):
        wizard = wizard_module.RouteGISWizard(interface)
        wizard.preview_canvas.setRenderFlag(False)
        wizard.drawing.setText(str(drawing))
        wizard.show()
        started = monotonic()
        wizard.next()
        assert monotonic() - started < 0.5
        assert wizard.project_type is None and wizard._analysis_task is None
        assert wizard.analyze_button.isEnabled()
        waiting, release = Event(), Event()
        ticks, percentages = [], []

        def delayed_analyze(service, path, **kwargs):
            notify = kwargs["on_progress"]

            def pause_at_read(percent, message):
                notify(percent, message)
                if percent == 5:
                    waiting.set()
                    assert release.wait(10), "UI heartbeat did not release worker"
                    notify(percent, message)  # Observe cancellation before parsing.

            kwargs["on_progress"] = pause_at_read
            return real_analyze(service, path, **kwargs)

        timer = QTimer()

        def heartbeat():
            ticks.append(wizard.analysis_elapsed.text())
            if waiting.is_set() and monotonic() - started > 1.2:
                assert "DXF" in wizard.analysis_log.toPlainText()
                assert 0 < wizard.analysis_progress.value() < 100
                assert not wizard.analyze_button.isEnabled()
                assert not wizard.button(QWizard.NextButton).isEnabled()
                if cancel:
                    wizard.reject()  # The bottom button cancels work, not the dialog.
                release.set()
                timer.stop()

        timer.timeout.connect(heartbeat)
        wizard.analysis_progress.valueChanged.connect(percentages.append)
        timer.start(50)
        try:
            with patch.object(service_class, "analyze_drawing", delayed_analyze):
                wizard.analyze_button.click()
                _wait_for_analysis(wizard)
            assert len(ticks) >= 5
            assert wizard.analyze_button.isEnabled() and not wizard._analysis_timer.isActive()
            if cancel:
                assert wizard.project_type is None and not wizard.service.last_cad_lines
                assert wizard._preview.line_layer is None
                assert "취소" in wizard.analysis_stage.text()
            else:
                assert wizard.project_type == "non_linear"
                assert wizard._preview.line_layer.featureCount() == 20_000
                assert wizard.analysis_progress.value() == 100
                assert percentages == sorted(percentages)
                assert "20,000" in wizard.analysis_log.toPlainText()
        finally:
            release.set()
            timer.stop()
            wizard.close()
            wizard.deleteLater()


def _assert_cad_preview(wizard_module, interface, temporary_root):
    import ezdxf
    from time import monotonic, sleep
    from unittest.mock import patch
    from pyproj import Transformer
    from qgis.PyQt.QtWidgets import QApplication
    from qgis.core import QgsProject

    x, y = Transformer.from_crs("EPSG:4326", "EPSG:5186", always_xy=True).transform(127.15, 35.82)
    drawing = temporary_root / "preview.dxf"
    document = ezdxf.new()
    document.units = 6
    document.modelspace().add_line((x - 500, y - 200), (x + 500, y + 200), dxfattribs={"layer": "ROAD"})
    document.modelspace().add_line((x - 500, y + 200), (x + 500, y - 200), dxfattribs={"layer": "RIVER"})
    document.saveas(drawing)
    existing_ids = set(QgsProject.instance().mapLayers())
    was_dirty = QgsProject.instance().isDirty()
    wizard = wizard_module.RouteGISWizard(interface)
    wizard.preview_canvas.setRenderFlag(False)
    try:
        wizard.business_region.setCurrentText("전라북도")
        wizard.drawing.setText(str(drawing))
        wizard.show()
        wizard.next()  # Navigation must not read the DXF on the GUI thread.
        QApplication.processEvents()
        assert wizard.currentId() == wizard._cad_page_id
        assert wizard.project_type is None and wizard._analysis_task is None
        wizard.analyze_button.click()
        _wait_for_analysis(wizard)
        assert wizard.project_type == "non_linear"
        assert wizard._crs_code(wizard.source_crs) == "EPSG:5186"
        assert wizard.crs_recommendations.count() > 0
        assert "DXF 실제 좌표" in wizard.coordinate_report.text()
        assert "127.150000" in wizard.preview_status.text()
        preview = wizard._preview
        assert preview.project is not QgsProject.instance()
        assert preview.project.layerTreeRoot().findGroup("11_기본도")
        assert all(layer.isValid() for layers in preview._basemaps.values() for layer in layers)
        assert "Base/service" in preview._basemaps["street"][0].source()
        assert "Hybrid/service" in preview._basemaps["hybrid"][0].source()
        assert "Satellite/service" in preview._basemaps["hybrid"][1].source()
        layer = preview.line_layer
        assert layer.featureCount() == 2
        assert {feature["dxf_layer"] for feature in layer.getFeatures()} == {"ROAD", "RIVER"}
        original_wkb = [bytes(feature.geometry().asWkb()) for feature in layer.getFeatures()]
        original_center = wizard.preview_canvas.extent().center()
        assert wizard.preview_canvas.layers() == [layer, *preview._basemaps["street"]]
        assert not wizard.preview_canvas.extent().isEmpty()
        _assert_preview_render(wizard, temporary_root)

        wizard.source_crs.setCurrentIndex(wizard.source_crs.findData("EPSG:5174"))
        deadline = monotonic() + 3
        while wizard._preview_timer.isActive() and monotonic() < deadline:
            QApplication.processEvents()
            sleep(0.01)
        assert not wizard._preview_timer.isActive()
        assert layer.crs().authid() == "EPSG:5174"
        assert wizard._crs_code(wizard.output_crs) == "EPSG:5174"
        assert abs(wizard.preview_canvas.extent().center().y() - original_center.y()) > 90000
        assert [bytes(feature.geometry().asWkb()) for feature in layer.getFeatures()] == original_wkb

        index = wizard.crs_recommendations.findData("EPSG:5186")
        wizard.crs_recommendations.setCurrentIndex(index)
        wizard.crs_recommendations.activated.emit(index)
        wizard._refresh_preview()
        assert layer.crs().authid() == "EPSG:5186"
        assert abs(wizard.preview_canvas.extent().center().y() - original_center.y()) < 1
        wizard.preview_basemap.setCurrentIndex(1)
        assert wizard.preview_canvas.layers() == [layer, *preview._basemaps["hybrid"]]

        wizard.source_crs.setCurrentText("not-a-crs")
        wizard._refresh_preview()
        assert not wizard.preview_canvas.layers()
        assert "실패" in wizard.preview_status.text()
        wizard.source_crs.setCurrentIndex(wizard.source_crs.findData("EPSG:5186"))
        wizard._refresh_preview()
        assert wizard.preview_canvas.layers()[0] is layer

        wizard.business_region.setCurrentText("전북특별자치도")
        assert wizard.project_type is None
        assert not wizard.preview_canvas.layers()
        assert preview.line_layer is None
        wizard._analyze()
        _wait_for_analysis(wizard)
        assert len(preview.project.mapLayers()) == 4  # One drawing, three basemap rasters.
        wizard.drawing.setText(str(temporary_root / "missing.dxf"))
        errors = []
        with patch.object(wizard_module.QMessageBox, "critical", lambda _, title, message: errors.append(message)):
            wizard._analyze()
            _wait_for_analysis(wizard)
        assert errors and wizard.project_type is None
        assert preview.line_layer is None and not wizard.preview_canvas.layers()
        assert wizard.crs_recommendations.count() == 0
        assert set(QgsProject.instance().mapLayers()) == existing_ids
        assert QgsProject.instance().isDirty() == was_dirty
    finally:
        wizard.close()
        QApplication.processEvents()
    assert not wizard._preview.project.mapLayers()
    assert not wizard._preview_timer.isActive()


def _assert_preview_render(wizard, temporary_root):
    """Render the actual CAD overlay above a deterministic georeferenced raster."""
    from osgeo import gdal, osr
    from qgis.PyQt.QtCore import QSize
    from qgis.core import QgsMapSettings, QgsMapRendererParallelJob, QgsRasterLayer

    extent = wizard.preview_canvas.extent()
    raster_path = temporary_root / "preview-background.tif"
    raster = gdal.GetDriverByName("GTiff").Create(str(raster_path), 64, 64, 3, gdal.GDT_Byte)
    raster.SetGeoTransform((extent.xMinimum(), extent.width() / 64, 0, extent.yMaximum(), 0, -extent.height() / 64))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(3857)
    raster.SetProjection(srs.ExportToWkt())
    for band, value in enumerate((50, 150, 80), start=1):
        raster.GetRasterBand(band).Fill(value)
    raster = None
    background = QgsRasterLayer(str(raster_path), "Offline reference map", "gdal")
    assert background.isValid()
    settings = QgsMapSettings(wizard.preview_canvas.mapSettings())
    settings.setOutputSize(QSize(320, 240))
    settings.setLayers([wizard._preview.line_layer, background])
    job = QgsMapRendererParallelJob(settings)
    job.start()
    job.waitForFinished()
    assert not job.errors()
    image = job.renderedImage()
    red = green = 0
    for x in range(0, image.width(), 2):
        for y in range(0, image.height(), 2):
            color = image.pixelColor(x, y)
            red += color.red() > 180 and color.green() < 90
            green += color.green() > 120 and color.red() < 90
    assert red > 50 and green > 1000, (red, green)


def _wait_for_build(wizard: Any, timeout: float = 30) -> None:
    from time import monotonic, sleep
    from qgis.PyQt.QtWidgets import QApplication

    deadline = monotonic() + timeout
    while wizard._build_task is not None and monotonic() < deadline:
        QApplication.processEvents()
        sleep(0.01)
    assert wizard._build_task is None, "background build did not finish within the smoke timeout"
    QApplication.processEvents()


def _click_build(wizard, wizard_module):
    from unittest.mock import patch

    wizard.setStartId(wizard._result_page_id)
    wizard.restart()
    assert wizard.run_button.isEnabled()
    errors = []
    with patch.object(wizard_module.QMessageBox, "critical", lambda _, title, message: errors.append(message)):
        wizard.run_button.click()
        _wait_for_build(wizard)
    assert not errors, errors
    assert wizard.result is not None, wizard.progress.toPlainText()
    assert wizard.progress_bar.value() == 100


def _assert_responsive_build(wizard_module, interface, project_root, temporary_root):
    from threading import Event, get_ident
    from time import monotonic
    from unittest.mock import patch
    from qgis.PyQt.QtCore import QTimer
    FixtureProvider = _core_module("cli").FixtureProvider

    main_thread = get_ident()
    for cancel in (False, True):
        waiting, release = Event(), Event()
        worker_threads = []

        class SlowProvider(FixtureProvider):
            def fetch_layer(self, definition, aoi):
                worker_threads.append(get_ident())
                waiting.set()
                assert release.wait(5), "UI did not respond while provider was waiting"
                return super().fetch_layer(definition, aoi)

        wizard = wizard_module.RouteGISWizard(interface)
        _configure_fixture_build(
            wizard, project_root, temporary_root / f"background-{cancel}",
            SlowProvider(project_root / "tests" / "fixtures" / "spatial_mock.json"),
        )
        wizard.setStartId(wizard._result_page_id)
        wizard.restart()
        wizard.show()
        observed = []
        receive = wizard._on_build_progress

        def record(percent, message):
            assert get_ident() == main_thread
            observed.append((percent, message))
            receive(percent, message)

        wizard._on_build_progress = record
        ticks = []
        started = monotonic()

        def heartbeat():
            ticks.append(wizard.build_elapsed.text())
            if waiting.is_set() and monotonic() - started >= 1.2:
                assert wizard.result is None
                assert "수집 중" in wizard.progress.toPlainText()
                assert 0 < wizard.progress_bar.value() < 100
                assert not wizard.run_button.isEnabled()
                if cancel:
                    wizard.reject()
                    assert wizard._build_task is not None
                release.set()
                timer.stop()

        timer = QTimer()
        timer.setInterval(20)
        timer.timeout.connect(heartbeat)
        timer.start()
        try:
            wizard._build()
            assert wizard._build_task is not None and wizard.result is None
            assert wizard.isVisible() and wizard.progress_bar.isVisible()
            _wait_for_build(wizard)
            assert len(ticks) >= 10
            assert any("00:01" in text for text in ticks)
            assert worker_threads and all(thread != main_thread for thread in worker_threads)
            assert observed and all("placeholder" not in message for _, message in observed)
            assert wizard.run_button.isEnabled() and not wizard._build_timer.isActive()
            if cancel:
                assert wizard.result is None
                assert "취소됨" in wizard.build_stage.text()
                assert not list((temporary_root / f"background-{cancel}").rglob("project_manifest.json"))
            else:
                assert wizard.result is not None and wizard.progress_bar.value() == 100
                assert "DXF" in wizard.progress.toPlainText()
                assert "보고서" in wizard.progress.toPlainText()
                _assert_loaded_project(importlib.import_module("qgis.core").QgsProject.instance(), wizard)
        finally:
            release.set()
            timer.stop()
            _wait_for_build(wizard)
            wizard._on_build_progress = receive
            wizard.close()
            wizard.deleteLater()
            timer.deleteLater()

    wizard = wizard_module.RouteGISWizard(interface)
    _configure_fixture_build(wizard, project_root, temporary_root / "background-failure")

    def fail(config, **kwargs):
        kwargs["on_progress"](8, f"DXF 읽는 중 {FAKE_VWORLD_KEY}")
        raise RuntimeError(f"deliberate worker failure {FAKE_VWORLD_KEY}")

    dialogs = []
    wizard.service.build = fail
    with patch.object(wizard_module.QMessageBox, "critical", lambda _, title, message: dialogs.append(message)):
        wizard._build()
        _wait_for_build(wizard)
    assert wizard.result is None and wizard.run_button.isEnabled()
    assert "실패" in wizard.build_stage.text()
    assert dialogs and "deliberate worker failure" in dialogs[0]
    assert FAKE_VWORLD_KEY not in wizard.progress.toPlainText() + dialogs[0]
    assert wizard.progress_bar.value() < 100
    wizard.close()
    wizard.deleteLater()


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

    route = project.mapLayersByName("선형")[0].renderer().symbol()
    assert abs(route.width() - 2) < 1e-9
    assert route.symbolLayer(0).widthUnit() == QgsUnitTypes.RenderPixels

    fill_styles = {
        "ecology_nature": ((76, 175, 80, 80), (46, 125, 50, 255), 0.8),
        "heritage_protection": ((156, 39, 176, 80), (106, 27, 154, 255), 0.8),
        "river_area": ((66, 165, 245, 70), (25, 118, 210, 255), 0.8),
    }
    line_styles = {
        "power_line": ((141, 74, 45, 255), 1.2),
    }
    marker_styles = {"power_support": ((141, 74, 45, 255), 2.5)}
    LayerCatalog = _core_module("providers").LayerCatalog

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
    write_geopackage = _core_module("gis.gpkg").write_geopackage
    write_shapefile = _core_module("gis.shapefile").write_shapefile
    SpatialFeature = _core_module("models").SpatialFeature
    SpatialLayer = _core_module("models").SpatialLayer
    _apply_native_renderer = _core_module("qgis.renderers")._apply_native_renderer
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
    LayerCatalog = _core_module("providers").LayerCatalog

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


def _assert_wizard_provider_requests(wizard_module: Any, interface: Any, project_root: Path, temporary_root: Path) -> None:
    """Exercise real Qt fields, credential resolution and all provider request paths."""
    from dataclasses import replace
    from unittest.mock import patch
    from urllib.parse import quote

    import httpx

    ProjectService = _core_module("app.project_service").ProjectService
    load_data_go_kr_service_key = _core_module("environment").load_data_go_kr_service_key
    load_vworld_api_key = _core_module("environment").load_vworld_api_key

    ecology_key = "qgis-smoke-ecology+/=placeholder"
    encoded_key = quote(ecology_key, safe="")
    dotenv = temporary_root / "provider.env"
    dotenv.write_text("VWORLD_API_KEY=stale-vworld\nDATA_GO_KR_SERVICE_KEY=stale-ecology\n", encoding="utf-8")
    real_client = httpx.Client
    requests = []
    failure = False

    def handler(request):
        requests.append(request)
        if request.url.host == "api.vworld.kr":
            assert request.url.params["key"] == FAKE_VWORLD_KEY
            if failure:
                return httpx.Response(200, text=f'<ServiceExceptionReport><ServiceException code="INVALID_KEY">{FAKE_VWORLD_KEY}</ServiceException></ServiceExceptionReport>')
            return httpx.Response(200, json={"type": "FeatureCollection", "features": []})
        if request.url.host == "apis.data.go.kr":
            assert request.url.params["serviceKey"] == ecology_key
            assert b"%25" not in request.url.query
            if failure:
                return httpx.Response(403, text=f'<OpenAPI_ServiceResponse><cmmMsgHeader><errMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</errMsg><returnReasonCode>30</returnReasonCode><returnAuthMsg>{ecology_key}</returnAuthMsg></cmmMsgHeader></OpenAPI_ServiceResponse>')
            return httpx.Response(200, text='<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs"/>')
        assert request.url.host == "overpass-api.de"
        if failure:
            raise httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] private-proxy-detail", request=request)
        return httpx.Response(200, json={"elements": []})

    def mock_client(**kwargs):
        # Keep the provider factory's TLS options, but isolate all proxy settings
        # so these requests always use the offline transport.
        kwargs["trust_env"] = False
        kwargs.pop("proxy", None)
        return real_client(**kwargs, transport=httpx.MockTransport(handler))

    with patch.object(wizard_module, "load_vworld_api_key", load_vworld_api_key), \
         patch.object(wizard_module, "load_data_go_kr_service_key", load_data_go_kr_service_key), \
         patch.object(wizard_module, "PLUGIN_DOTENV", dotenv), \
         patch.dict(os.environ, {"VWORLD_API_KEY": "stale-vworld", "DATA_GO_KR_SERVICE_KEY": "stale-ecology"}), \
         patch.object(httpx, "Client", mock_client):
        wizard = wizard_module.RouteGISWizard(interface)
        wizard.service = ProjectService(replace(wizard.settings, osm_retry=0))
        wizard.drawing.setText(str(project_root / "tests" / "fixtures" / "sample_route.dxf"))
        wizard.output_directory.setText(str(temporary_root / "provider-output"))
        wizard.vworld_api_key.setText(FAKE_VWORLD_KEY)
        wizard.data_go_kr_service_key.setText(encoded_key)
        wizard._analyze()
        _wait_for_analysis(wizard)
        for failure in (False, True):
            requests.clear()
            wizard.project_name.setText(f"Provider requests {failure}")
            wizard._build()
            _wait_for_build(wizard)
            assert wizard.result is not None
            expected_status = "failed" if failure else "success"
            assert len(wizard.result.downloads) == len(DEFAULT_LAYER_IDS)
            assert all(result.status == expected_status for result in wizard.result.downloads), [
                (result.layer_id, result.status, result.error) for result in wizard.result.downloads
            ]
            assert {request.url.host for request in requests} == {"api.vworld.kr", "apis.data.go.kr", "overpass-api.de"}
            osm_requests = [request for request in requests if request.url.host == "overpass-api.de"]
            assert len(osm_requests) == (3 if failure else 2)
            assert all(b'way["waterway"~' not in request.content for request in osm_requests)
            text = wizard.progress.toPlainText()
            if failure:
                for detail in ("INVALID_KEY", "HTTP 403", "SERVICE_KEY_IS_NOT_REGISTERED_ERROR", "인증서", "모두 실패"):
                    assert detail in text
            else:
                assert "해당없음" in text
            assert str(wizard.result.report_html) in text
            for artifact in (wizard.result.manifest, wizard.result.report_html, wizard.result.project_directory / "logs" / "app.log"):
                text += artifact.read_text(encoding="utf-8")
            for secret in (FAKE_VWORLD_KEY, ecology_key, encoded_key, "private-proxy-detail"):
                assert secret not in text
        wizard.close()
        # Keep the existing reopen/persistence assertion's expected credentials.
        wizard.data_go_kr_service_key.setText(FAKE_ECOLOGY_KEY)
        wizard._save_credentials()
        wizard.deleteLater()


def _assert_headless_project_layout(project_root: Path, temporary_root: Path) -> None:
    from unittest.mock import patch

    ProjectService = _core_module("app.project_service").ProjectService
    FixtureProvider = _core_module("cli").FixtureProvider
    ProjectConfig = _core_module("models").ProjectConfig
    LayerCatalog = _core_module("providers").LayerCatalog
    from qgis.core import QgsProject

    with patch.object(_core_module("qgis.project"), "_native_project", return_value=False):
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


def _assert_layer_crs_updates(project_root: Path, temporary_root: Path) -> None:
    from time import monotonic

    from qgis.PyQt import sip
    from qgis.PyQt.QtCore import QCoreApplication, QEvent, QObject
    from qgis.PyQt.QtTest import QTest
    from qgis.PyQt.QtWidgets import QDialogButtonBox
    from qgis.core import QgsCoordinateReferenceSystem, QgsProject
    from qgis.gui import QgsMapCanvas, QgsMessageBar, QgsProjectionSelectionWidget, QgsVectorLayerProperties

    ProjectService = _core_module("app.project_service").ProjectService
    ProjectConfig = _core_module("models").ProjectConfig
    project = QgsProject.instance()
    original_crs = QgsCoordinateReferenceSystem("EPSG:5186")
    changed_crs = QgsCoordinateReferenceSystem("EPSG:5187")
    canvas = QgsMapCanvas()
    canvas.resize(640, 480)
    canvas.setProject(project)
    canvas.setCachingEnabled(True)
    rendered = []
    canvas.mapCanvasRefreshed.connect(lambda: rendered.append(True))

    def wait_for_render(previous_count, previous_image=None):
        deadline = monotonic() + 5
        while monotonic() < deadline:
            QTest.qWait(10)
            if len(rendered) > previous_count and not canvas.isDrawing():
                image = canvas.grab().toImage()
                if previous_image is None or image != previous_image:
                    return image
        raise AssertionError("Map did not finish rendering the changed layer CRS")

    try:
        canvas.show()
        for native in (False, True):
            for shapefile in (True, False):
                result = ProjectService().build(
                    ProjectConfig(
                        project_name=f"Layer CRS {native} {shapefile}",
                        source_drawing=project_root / "tests" / "fixtures" / "sample_route.dxf",
                        source_layers=(), source_crs="EPSG:5186", output_crs="EPSG:5186",
                        buffer_distance=1000, selected_layers=(), project_type="non_linear",
                        output_directory=temporary_root / "layer-crs", export_shapefile=shapefile,
                    ),
                    use_native_project=native,
                )
                assert project.read(str(result.project_file))
                assert project.crs() == original_crs, "Generated project lost its CRS; reprojection is disabled"
                canvas.setDestinationCrs(project.crs())
                layer = project.mapLayersByName("선형")[0]
                coordinates = next(layer.getFeatures()).geometry().asWkb()
                canvas.setLayers([layer])
                extent = layer.extent()
                extent.scale(1.2)
                canvas.setExtent(extent)
                previous_count = len(rendered)
                canvas.refresh()
                before = wait_for_render(previous_count)

                properties = QgsVectorLayerProperties(canvas, QgsMessageBar(canvas), layer, canvas)
                selector = properties.findChild(QObject, "mCrsSelector")
                assert selector is not None
                selector = sip.cast(selector, QgsProjectionSelectionWidget)
                selector.setCrs(changed_crs)
                previous_count = len(rendered)
                apply_buttons = [
                    box.button(QDialogButtonBox.Apply)
                    for box in properties.findChildren(QDialogButtonBox)
                    if box.button(QDialogButtonBox.Apply) is not None
                ]
                assert len(apply_buttons) == 1
                apply_buttons[0].click()
                wait_for_render(previous_count, before)
                assert next(layer.getFeatures()).geometry().asWkb() == coordinates
                assert project.crs() == original_crs
                assert layer.crs() == changed_crs
                properties.close()
                properties.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

                assert project.write()
                canvas.stopRendering()
                canvas.setLayers([])
                assert project.read(str(result.project_file))
                reopened = project.mapLayersByName("선형")[0]
                assert reopened.crs() == changed_crs
                assert project.crs() == original_crs
                project.clear()
    finally:
        canvas.stopRendering()
        canvas.setLayers([])
        canvas.close()
        canvas.deleteLater()
        project.clear()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def _assert_empty_result_group_labels(project_root: Path, temporary_root: Path) -> None:
    from contextlib import nullcontext
    from unittest.mock import patch

    ProjectService = _core_module("app.project_service").ProjectService
    FixtureProvider = _core_module("cli").FixtureProvider
    ProjectConfig = _core_module("models").ProjectConfig
    LayerCatalog = _core_module("providers").LayerCatalog
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
        context = nullcontext() if native else patch.object(_core_module("qgis.project"), "_native_project", return_value=False)
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


def _load_plugin(interface, plugin_directory, temporary_root, previous_plugin_directory):
    from qgis import utils

    package = plugin_directory.name
    release_directory = plugin_directory
    if previous_plugin_directory is not None:
        plugin_directory = temporary_root / "plugins" / package
        shutil.copytree(previous_plugin_directory, plugin_directory, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    sys.path.insert(0, str(plugin_directory.parent))
    utils.available_plugins.append(package)
    utils.iface = interface
    if previous_plugin_directory is not None:
        assert utils.loadPlugin(package) and utils.startPlugin(package)
        utils.plugins[package].run()
        previous_service = utils.plugins[package].wizard.service
        from inspect import signature
        print(f"Upgrade source: {_plugin_version(plugin_directory)}, build{signature(previous_service.build)}")
        assert utils.unloadPlugin(package)
        # QGIS only tracks modules below the plugin package. The old global
        # routegis modules survive unloading, even when files are replaced.
        previous_module = previous_service.__class__.__module__
        assert (previous_module in sys.modules) == (not previous_module.startswith(f"{package}."))
        shutil.rmtree(plugin_directory)
        shutil.copytree(release_directory, plugin_directory, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        importlib.invalidate_caches()
    assert utils.loadPlugin(package) and utils.startPlugin(package)
    return utils.plugins[package], plugin_directory


def run(project_root: Path, plugin_directory: Path, previous_plugin_directory: Path | None = None) -> None:
    global CORE_PACKAGE

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
            interface = _Interface(QMainWindow())
            plugin, plugin_directory = _load_plugin(interface, plugin_directory, temporary_root, previous_plugin_directory)
            CORE_PACKAGE = f"{plugin_directory.name}.routegis"
            core = _core_module()
            EcologyClient = _core_module("providers.ecology").EcologyClient
            OverpassClient = _core_module("providers.overpass").OverpassClient
            VWorldClient = _core_module("providers.vworld").VWorldClient

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

            assert len(interface.toolbar_actions) == len(interface.menu_actions) == 1
            assert not plugin.action.icon().isNull()
            plugin.run()
            application.processEvents()
            wizard = plugin.wizard
            assert wizard is not None and wizard.isVisible()
            _assert_wizard_catalog(wizard)
            _assert_wizard_crs_choices(wizard)
            _assert_cad_preview(wizard_module, interface, temporary_root)
            _assert_responsive_analysis(wizard_module, interface, temporary_root)

            _configure_fixture_build(wizard, project_root, temporary_root / "output")
            from inspect import signature
            print(f"Loaded core: {core.__version__}, build{signature(type(wizard.service).build)}")
            _click_build(wizard, wizard_module)
            assert core.__version__ == EXPECTED_VERSION
            assert type(wizard.service).__module__ == f"{CORE_PACKAGE}.app.project_service"
            service_path = sys.modules[type(wizard.service).__module__].__file__
            assert service_path and Path(service_path).resolve().is_relative_to(plugin_directory)
            assert [result.layer_id for result in wizard.result.downloads] == list(DEFAULT_LAYER_IDS)
            assert all(result.status == "success" for result in wizard.result.downloads)
            assert wizard.result.project_file.is_file()
            _assert_no_placeholder_leak(wizard.result)
            _assert_loaded_project(QgsProject.instance(), wizard)
            _assert_layer_crs_updates(project_root, temporary_root)
            _assert_native_category_edge_values(temporary_root)
            _assert_headless_project_layout(project_root, temporary_root)
            _assert_empty_result_group_labels(project_root, temporary_root)
            _assert_wizard_provider_requests(wizard_module, interface, project_root, temporary_root)
            _assert_responsive_build(wizard_module, interface, project_root, temporary_root)

            wizard.close()
            application.processEvents()
            plugin.run()
            application.processEvents()
            reopened_wizard = plugin.wizard
            assert reopened_wizard is not None and reopened_wizard.isVisible()
            assert reopened_wizard.vworld_api_key.text() == FAKE_VWORLD_KEY
            assert reopened_wizard.data_go_kr_service_key.text() == FAKE_ECOLOGY_KEY

            from qgis import utils
            previous_class = type(reopened_wizard.service)
            assert utils.reloadPlugin(plugin_directory.name)
            plugin = utils.plugins[plugin_directory.name]
            plugin.run()
            assert type(plugin.wizard.service) is not previous_class
            _configure_fixture_build(plugin.wizard, project_root, temporary_root / "reloaded-output")
            _click_build(plugin.wizard, importlib.import_module(f"{plugin_directory.name}.wizard"))
            _assert_loaded_project(QgsProject.instance(), plugin.wizard)
        finally:
            if plugin is not None:
                from qgis import utils
                assert utils.unloadPlugin(plugin_directory.name)
                assert f"{CORE_PACKAGE}.app.project_service" not in sys.modules
            QgsProject.instance().clear()
            if application is not None:
                application.processEvents()
                from qgis.PyQt.QtCore import QCoreApplication, QEvent
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            QgsApplication.exitQgis()


def main() -> int:
    args = _arguments()
    try:
        run(args.project_root, args.plugin_dir, args.previous_plugin_dir)
    except Exception as exc:
        print(f"QGIS smoke failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1
    print(f"QGIS 3.34.3 RouteGIS {EXPECTED_VERSION} smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
