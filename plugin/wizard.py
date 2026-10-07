from __future__ import annotations

from pathlib import Path

from qgis.PyQt.QtCore import QSettings, Qt
from qgis.PyQt.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWizard,
    QWizardPage,
    QWidget,
)
from qgis.core import QgsApplication, QgsProject, QgsTask

from routegis.app.project_service import ProjectService
from routegis.config import load_profile, load_settings
from routegis.environment import load_data_go_kr_service_key, load_vworld_api_key
from routegis.errors import RouteGISError
from routegis.gis.crs import KOREAN_PROJECTED_CRS
from routegis.models import ProjectConfig
from routegis.providers.catalog import layer_tooltip_lines
from routegis.providers.vworld.client import VWorldClient
from routegis.security import redact_sensitive_text


PLUGIN_DOTENV = Path(__file__).resolve().parent / ".env"


class RouteGISWizard(QWizard):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.settings = load_settings()
        self.recent = QSettings("RouteGIS", "AutoBuilder")
        self.service = ProjectService(self.settings)
        self.result = None
        self.project_type = None
        self._vworld_task = None
        self._vworld_checked_ids: set[str] = set()
        self._vworld_cursor_active = False
        self.setWindowTitle("RouteGIS AutoBuilder")
        self.setWizardStyle(QWizard.ModernStyle)
        self.setMinimumSize(760, 600)
        self.addPage(self._project_page())
        self._cad_page_id = self.addPage(self._cad_page())
        self.addPage(self._analysis_page())
        self.addPage(self._result_page())
        self.source_crs.currentTextChanged.connect(self.output_crs.setCurrentText)

    @staticmethod
    def _path_row(line_edit: QLineEdit, callback) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        button = QPushButton("찾기")
        button.clicked.connect(callback)
        layout.addWidget(line_edit, 1)
        layout.addWidget(button)
        return widget

    def _project_page(self) -> QWizardPage:
        page = QWizardPage()
        page.setTitle("1. 프로젝트 생성")
        page.setSubTitle(
            "사업명, 저장 위치와 DXF 노선도면을 선택합니다. 서비스 키는 "
            "환경변수, 플러그인 .env, 저장된 입력 순서로 적용합니다."
        )
        layout = QFormLayout(page)
        self.project_name = QLineEdit()
        self.project_name.setPlaceholderText("예: OO 고속도로 기본설계")
        self.output_directory = QLineEdit(str(self.recent.value("output_directory", "")))
        self.drawing = QLineEdit(str(self.recent.value("drawing", "")))
        self.vworld_api_key = QLineEdit(str(self.recent.value("vworld_api_key", "")))
        self.vworld_api_key.setEchoMode(QLineEdit.Password)
        self.vworld_api_key.setPlaceholderText(".env 미설정 시 VWorld API Key 입력")
        self.data_go_kr_service_key = QLineEdit(
            str(self.recent.value("data_go_kr_service_key", ""))
        )
        self.data_go_kr_service_key.setEchoMode(QLineEdit.Password)
        self.data_go_kr_service_key.setPlaceholderText(
            ".env 미설정 시 공공데이터포털 서비스키 입력"
        )
        layout.addRow("사업명", self.project_name)
        layout.addRow("프로젝트 저장 위치", self._path_row(self.output_directory, self._choose_output))
        layout.addRow("DXF 노선도면", self._path_row(self.drawing, self._choose_drawing))
        layout.addRow("VWorld API Key 입력", self.vworld_api_key)
        layout.addRow("공공데이터포털 서비스키 입력", self.data_go_kr_service_key)
        self.drawing.textChanged.connect(self._invalidate_analysis)
        self.vworld_api_key.editingFinished.connect(self._save_credentials)
        self.data_go_kr_service_key.editingFinished.connect(self._save_credentials)
        return page

    @staticmethod
    def _crs_selector(selected_crs: str) -> QComboBox:
        selector = QComboBox()
        selector.setEditable(True)
        for code, name in KOREAN_PROJECTED_CRS:
            selector.addItem(f"{code} — {name}", code)
        index = selector.findData(selected_crs)
        if index >= 0:
            selector.setCurrentIndex(index)
        else:
            selector.setCurrentText(selected_crs)
        return selector

    @staticmethod
    def _crs_code(selector: QComboBox) -> str:
        return selector.currentText().split(" — ", 1)[0].strip().upper()

    def _cad_page(self) -> QWizardPage:
        page = QWizardPage()
        page.setTitle("2. 도면 분석")
        page.setSubTitle(
            "원본 좌표계를 확인하고 도면을 분석합니다. 모든 지원 선 객체를 Buffer 기준으로 사용합니다."
        )
        layout = QVBoxLayout(page)
        form = QFormLayout()
        self.source_crs = self._crs_selector("EPSG:5186")
        form.addRow("원본 도면 좌표계", self.source_crs)
        layout.addLayout(form)
        analyze = QPushButton("도면 분석")
        analyze.clicked.connect(self._analyze)
        self.candidate_table = QTableWidget(0, 4)
        self.candidate_table.setHorizontalHeaderLabels(["CAD 레이어", "객체 유형", "객체 수", "총 길이"])
        self.candidate_table.setSelectionMode(QTableWidget.NoSelection)
        self.candidate_table.setSortingEnabled(False)
        self.candidate_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(analyze)
        layout.addWidget(self.candidate_table)
        self.cad_status = QLabel("도면을 분석하면 Buffer 기준으로 사용할 모든 선 객체를 확인합니다.")
        self.cad_status.setWordWrap(True)
        layout.addWidget(self.cad_status)
        return page

    def _analysis_page(self) -> QWizardPage:
        page = QWizardPage()
        page.setTitle("3. 좌표계, 분석 범위와 공간정보")
        page.setSubTitle("좌표계는 시스템이 임의로 확정하지 않습니다. 도면에 맞는 좌표계를 확인해 주세요.")
        layout = QVBoxLayout(page)
        form = QFormLayout()
        self.output_crs = self._crs_selector(self._crs_code(self.source_crs))
        self.buffer_distance = QDoubleSpinBox()
        self.buffer_distance.setRange(1, 100000)
        self.buffer_distance.setDecimals(1)
        self.buffer_distance.setSuffix(" m")
        self.buffer_distance.setValue(self.settings.default_buffer)
        form.addRow("결과 좌표계", self.output_crs)
        form.addRow("사업구간 주변 범위", self.buffer_distance)
        layout.addLayout(form)
        layout.addWidget(QLabel("가져올 공간정보"))
        layout.addWidget(
            QLabel(
                "필요한 항목을 체크하세요. 공급원, 발행기관, 데이터셋, 분야, 이름, "
                "동의어 또는 레이어 ID로 검색할 수 있습니다."
            )
        )
        self.layer_search = QLineEdit()
        self.layer_search.setPlaceholderText("공간정보 검색 (예: 문화재, 행정, cadastral)")
        self.layer_search.setClearButtonEnabled(True)
        search_row = QWidget()
        search_layout = QHBoxLayout(search_row)
        search_layout.setContentsMargins(0, 0, 0, 0)
        search_layout.addWidget(self.layer_search, 1)
        self.load_vworld_button = QPushButton("브이월드 목록 불러오기")
        self.load_vworld_button.clicked.connect(self._load_vworld_catalog)
        search_layout.addWidget(self.load_vworld_button)
        layout.addWidget(search_row)
        self.layer_list = QListWidget()
        self.layer_list.setSelectionMode(QListWidget.NoSelection)
        self.layer_list.setUniformItemSizes(True)
        layout.addWidget(self.layer_list, 1)
        self.layer_status = QLabel()
        self.layer_status.setWordWrap(True)
        layout.addWidget(self.layer_status)
        self.layer_search.textChanged.connect(self._filter_layer_list)
        self.layer_list.itemChanged.connect(self._update_layer_status)
        self._render_layer_list(set(load_profile()))
        return page

    def _result_page(self) -> QWizardPage:
        page = QWizardPage()
        page.setTitle("4. 프로젝트 생성")
        page.setSubTitle("선택한 설정으로 공간정보와 QGIS 프로젝트를 생성합니다.")
        layout = QVBoxLayout(page)
        self.run_button = QPushButton("프로젝트 생성")
        self.run_button.clicked.connect(self._build)
        self.open_button = QPushButton("QGIS에서 열기")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self._open_project)
        self.progress = QTextEdit()
        self.progress.setReadOnly(True)
        layout.addWidget(self.run_button)
        layout.addWidget(self.progress, 1)
        layout.addWidget(self.open_button)
        return page

    def _choose_output(self):
        path = QFileDialog.getExistingDirectory(self, "프로젝트 저장 위치")
        if path:
            self.output_directory.setText(path)

    def _choose_drawing(self):
        path, _ = QFileDialog.getOpenFileName(self, "노선도면", "", "DXF 도면 (*.dxf)")
        if path:
            self.drawing.setText(path)

    def _drawing_path(self) -> Path:
        drawing = Path(self.drawing.text().strip())
        if drawing.suffix.lower() != ".dxf":
            raise RouteGISError("노선도면은 DXF 파일을 선택해 주세요.")
        return drawing

    def _output_directory(self) -> Path:
        output_directory = self.output_directory.text().strip()
        if not output_directory:
            raise RouteGISError("프로젝트 저장 위치를 선택해 주세요.")
        return Path(output_directory)

    def _save_credentials(self) -> None:
        credentials = {
            "vworld_api_key": self.vworld_api_key.text().strip(),
            "data_go_kr_service_key": self.data_go_kr_service_key.text().strip(),
        }
        for key, value in credentials.items():
            if value:
                self.recent.setValue(key, value)
            else:
                self.recent.remove(key)
        self.recent.sync()

    def _invalidate_analysis(self, *_: object) -> None:
        self.project_type = None
        self.candidate_table.setRowCount(0)
        self.cad_status.setText("도면이 변경되었습니다. 다시 분석해 주세요.")

    def _analyze(self):
        self.project_type = None
        try:
            candidates = self.service.analyze_drawing(self._drawing_path())
        except (RouteGISError, OSError, ValueError) as exc:
            QMessageBox.critical(self, "도면 분석 실패", str(exc))
            return
        self.candidate_table.setRowCount(len(candidates))
        for row, candidate in enumerate(candidates):
            values = (
                candidate["name"],
                ", ".join(candidate["entity_types"]),
                str(candidate["entity_count"]),
                f"{candidate['total_length'] / 1000:.3f} km",
            )
            for column, value in enumerate(values):
                self.candidate_table.setItem(row, column, QTableWidgetItem(value))
        if not candidates:
            self.cad_status.setText("지원하는 선 객체가 없습니다.")
            QMessageBox.warning(self, "도면 분석 결과", "LINE/POLYLINE/LWPOLYLINE/ARC 객체가 없습니다.")
            return
        warning_suffix = ""
        if self.service.last_cad_warnings:
            warning_suffix = "\n경고:\n" + "\n".join(
                f"- {issue.layer}: {issue.message}" for issue in self.service.last_cad_warnings
            )
        self.project_type = "non_linear"
        self.cad_status.setText(
            "표시된 모든 지원 선 객체를 기준으로 사업구간 주변 범위를 만듭니다."
            + warning_suffix
        )

    def validateCurrentPage(self) -> bool:
        if self.currentId() == self._cad_page_id and self.project_type is None:
            QMessageBox.warning(self, "도면 분석 필요", "도면 분석을 실행해 주세요.")
            return False
        return super().validateCurrentPage()

    def _render_layer_list(self, checked_ids: set[str] | None = None) -> None:
        checked_ids = checked_ids if checked_ids is not None else set(self._selected_layers())
        definitions = sorted(
            self.service.catalog.all(),
            key=self.service.catalog.sort_key,
        )
        self.layer_list.blockSignals(True)
        try:
            self.layer_list.clear()
            for definition in definitions:
                taxonomy = self.service.catalog.taxonomy_for(definition)
                label = (
                    f"[{taxonomy.reference_category}] {definition.name}"
                    if taxonomy is not None
                    else definition.name
                )
                item = QListWidgetItem(label)
                item.setData(Qt.UserRole, definition.id)
                item.setData(Qt.UserRole + 1, definition.data_id)
                item.setToolTip(
                    "\n".join(layer_tooltip_lines(definition, taxonomy))
                )
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if definition.id in checked_ids else Qt.Unchecked)
                self.layer_list.addItem(item)
        finally:
            self.layer_list.blockSignals(False)
        self._filter_layer_list(self.layer_search.text())

    @staticmethod
    def _discover_vworld_catalog(task, settings, api_key: str):
        client: VWorldClient | None = None
        definitions = None
        error = None
        try:
            if task.isCanceled():
                return None
            service = ProjectService(settings)
            provider = service._provider(
                provider_name="vworld",
                api_key=api_key,
                data_go_kr_service_key="",
                domain=None,
            )
            if not isinstance(provider, VWorldClient):
                raise RouteGISError("VWorld 목록 공급자를 초기화할 수 없습니다.")
            client = provider
            definitions = client.discover_layers()
        except Exception as exc:
            error = redact_sensitive_text(exc, api_key)
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception as exc:
                    if error is None:
                        error = redact_sensitive_text(exc, api_key)
            api_key = ""

        if task.isCanceled():
            return None
        if error is not None:
            return {"error": error}
        return {"definitions": definitions}

    def _finish_vworld_catalog(self, exception, result=None) -> None:
        try:
            if exception is not None:
                message = "현재 목록을 유지합니다. VWorld 목록 작업 중 예기치 못한 오류가 발생했습니다."
                self.layer_status.setText("목록 유지 · 불러오기 실패")
                QMessageBox.warning(self, "브이월드 목록 불러오기 실패", message)
                return
            if result is None:
                self.layer_status.setText("현재 목록 유지 · 불러오기 취소")
                return
            error = result.get("error")
            if error:
                message = f"현재 {self.layer_list.count()}개 목록을 유지합니다.\n{error}"
                self.layer_status.setText(f"목록 유지 · 불러오기 실패: {error}")
                QMessageBox.warning(self, "브이월드 목록 불러오기 실패", message)
                return

            definitions = result.get("definitions") or []
            added = self.service.catalog.merge_live(definitions)
            self._render_layer_list(self._vworld_checked_ids)
            total = len(self.service.catalog.all())
            self.layer_status.setText(
                f"브이월드 목록 {total}개 · 신규 {added}개 · "
                f"선택 {len(self._vworld_checked_ids)}개"
            )
        finally:
            self._vworld_task = None
            self._vworld_checked_ids = set()
            if self._vworld_cursor_active:
                QApplication.restoreOverrideCursor()
                self._vworld_cursor_active = False
            self.load_vworld_button.setEnabled(True)

    def _load_vworld_catalog(self) -> None:
        self._save_credentials()
        api_key = load_vworld_api_key(PLUGIN_DOTENV) or self.vworld_api_key.text().strip()
        if not api_key:
            message = "프로젝트 화면에 VWorld API Key를 입력하거나 플러그인 .env에 설정해 주세요."
            self.layer_status.setText(f"현재 목록 유지 · {message}")
            QMessageBox.warning(self, "브이월드 목록 불러오기", message)
            return

        self._vworld_checked_ids = set(self._selected_layers())
        self.load_vworld_button.setEnabled(False)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        self._vworld_cursor_active = True
        self.layer_status.setText("브이월드 목록을 불러오는 중입니다…")
        try:
            self._vworld_task = QgsTask.fromFunction(
                "RouteGIS VWorld 목록 불러오기",
                self._discover_vworld_catalog,
                on_finished=self._finish_vworld_catalog,
                settings=self.settings,
                api_key=api_key,
            )
            QgsApplication.taskManager().addTask(self._vworld_task)
        except Exception as exc:
            safe_error = redact_sensitive_text(exc, api_key) or "알 수 없는 오류"
            message = f"현재 {self.layer_list.count()}개 목록을 유지합니다.\n{safe_error}"
            self.layer_status.setText(f"목록 유지 · 불러오기 실패: {safe_error}")
            QMessageBox.warning(self, "브이월드 목록 불러오기 실패", message)
            self._vworld_task = None
            self._vworld_checked_ids = set()
            if self._vworld_cursor_active:
                QApplication.restoreOverrideCursor()
                self._vworld_cursor_active = False
            self.load_vworld_button.setEnabled(True)
        finally:
            api_key = ""

    def _filter_layer_list(self, text: str) -> None:
        for index in range(self.layer_list.count()):
            item = self.layer_list.item(index)
            layer_id = str(item.data(Qt.UserRole))
            item.setHidden(not self.service.catalog.matches(self.service.catalog.get(layer_id), text))
        self._update_layer_status()

    def _update_layer_status(self, *_: object) -> None:
        total = self.layer_list.count()
        checked = sum(
            self.layer_list.item(index).checkState() == Qt.Checked
            for index in range(total)
        )
        visible = sum(not self.layer_list.item(index).isHidden() for index in range(total))
        suffix = f" · 검색 결과 {visible}" if visible != total else ""
        self.layer_status.setText(f"선택 {checked}/{total}{suffix}")

    def _selected_layers(self) -> tuple[str, ...]:
        checked_ids = tuple(
            str(self.layer_list.item(index).data(Qt.UserRole))
            for index in range(self.layer_list.count())
            if self.layer_list.item(index).checkState() == Qt.Checked
        )
        default_ids = load_profile()
        checked = set(checked_ids)
        return tuple(layer_id for layer_id in default_ids if layer_id in checked) + tuple(
            layer_id for layer_id in checked_ids if layer_id not in default_ids
        )

    def _build(self):
        self.run_button.setEnabled(False)
        self.progress.setPlainText("노선과 분석 범위를 생성하고 있습니다…")
        QApplication.processEvents()
        self._save_credentials()
        api_key = ""
        data_go_kr_service_key = ""
        try:
            if self.project_type is None:
                raise RouteGISError("도면 분석을 실행해 주세요.")
            config = ProjectConfig(
                project_name=self.project_name.text().strip(),
                source_drawing=self._drawing_path(),
                source_layers=(),
                source_crs=self._crs_code(self.source_crs),
                output_crs=self._crs_code(self.output_crs),
                buffer_distance=self.buffer_distance.value(),
                selected_layers=self._selected_layers(),
                output_directory=self._output_directory(),
                export_shapefile=True,
                snap_tolerance=self.settings.snap_tolerance,
                chord_tolerance=self.settings.chord_tolerance,
                project_type="non_linear",
            )
            api_key = (
                load_vworld_api_key(PLUGIN_DOTENV)
                or self.vworld_api_key.text().strip()
            )
            data_go_kr_service_key = (
                load_data_go_kr_service_key(PLUGIN_DOTENV)
                or self.data_go_kr_service_key.text().strip()
            )
            self.result = self.service.build(
                config,
                api_key=api_key,
                data_go_kr_service_key=data_go_kr_service_key,
                domain=None,
            )
            self.recent.setValue("output_directory", self.output_directory.text())
            self.recent.setValue("drawing", self.drawing.text())
            self.recent.remove("domain")
            self.recent.sync()
            lines = [
                "✓ 선형 레이어 생성",
                "✓ 분석범위 생성",
                *[
                    ("✓ " if item.status == "success" else "⚠ ") + f"{item.layer_id}: {item.status}"
                    for item in self.result.downloads
                ],
                "✓ GeoPackage 생성",
                "✓ SHP 생성",
                "✓ QGIS 프로젝트 생성",
                f"\n{self.result.state.value}",
            ]
            self.progress.setPlainText("\n".join(lines))
            self.open_button.setEnabled(True)
            self._open_project()
        except Exception as exc:
            safe_error = (
                redact_sensitive_text(exc, api_key, data_go_kr_service_key)
                or "알 수 없는 오류"
            )
            self.progress.append(f"\n오류: {safe_error}")
            QMessageBox.critical(self, "프로젝트 생성 실패", safe_error)
        finally:
            api_key = ""
            data_go_kr_service_key = ""
            self.run_button.setEnabled(True)

    def _open_project(self):
        if self.result is None or self.result.project_file is None:
            return
        if not self.iface.addProject(str(self.result.project_file)):
            QMessageBox.critical(self, "프로젝트 열기 실패", "생성된 QGIS 프로젝트를 열 수 없습니다.")
            return
        route_layers = QgsProject.instance().mapLayersByName("선형")
        if not route_layers:
            route_layers = QgsProject.instance().mapLayersByName("노선 중심선")
        if route_layers:
            self.iface.mapCanvas().setExtent(route_layers[0].extent())
        self.iface.mapCanvas().refresh()

    def closeEvent(self, event) -> None:
        self._save_credentials()
        if self._vworld_task is not None:
            self._vworld_task.cancel()
        super().closeEvent(event)
