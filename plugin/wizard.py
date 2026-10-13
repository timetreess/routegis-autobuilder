from __future__ import annotations

from pathlib import Path
from time import monotonic

from qgis.PyQt.QtCore import QSettings, Qt, QTimer
from qgis.PyQt.QtGui import QTextCursor
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
    QProgressBar,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWizard,
    QWizardPage,
    QWidget,
)
from qgis.core import QgsApplication, QgsProject, QgsTask

from .routegis.app.project_service import ProjectService
from .routegis.config import load_profile, load_settings
from .routegis.environment import load_data_go_kr_service_key, load_vworld_api_key
from .routegis.errors import RouteGISError
from .routegis.gis.crs import KOREAN_PROJECTED_CRS, validate_projected_metre_crs
from .routegis.gis.crs_recommendation import BUSINESS_REGIONS, recommend_crs
from .routegis.models import ProjectConfig
from .routegis.providers.catalog import layer_tooltip_lines
from .routegis.providers.vworld.client import VWorldClient
from .routegis.qgis.cad_preview import CadMapPreview
from .routegis.security import redact_sensitive_text

from .build_task import ProjectBuildTask


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
        self._vworld_cursor_active = False
        self._build_task = None
        self._build_started = 0.0
        self._last_build_update = 0.0
        self._build_cancel_requested = False
        self.setWindowTitle("RouteGIS AutoBuilder")
        self.setWizardStyle(QWizard.ModernStyle)
        self.setMinimumSize(860, 700)
        self.addPage(self._project_page())
        self._cad_page_id = self.addPage(self._cad_page())
        self.addPage(self._analysis_page())
        self._result_page_id = self.addPage(self._result_page())
        self.source_crs.currentTextChanged.connect(self.output_crs.setCurrentText)
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(300)
        self._preview_timer.timeout.connect(self._refresh_preview)
        self.source_crs.currentTextChanged.connect(self._schedule_preview)
        self.finished.connect(self._dispose_preview)
        self._build_timer = QTimer(self)
        self._build_timer.setInterval(1000)
        self._build_timer.timeout.connect(self._update_build_elapsed)

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
            "입력란의 값(저장된 입력 포함)을 우선 사용하며, 비어 있으면 환경변수, 플러그인 .env 순서로 적용합니다."
        )
        layout = QFormLayout(page)
        self.project_name = QLineEdit()
        self.project_name.setPlaceholderText("예: OO 고속도로 기본설계")
        self.business_region = QComboBox()
        self.business_region.setEditable(True)
        self.business_region.setInsertPolicy(QComboBox.NoInsert)
        self.business_region.addItem("")
        self.business_region.addItems([region.name for region in BUSINESS_REGIONS])
        self.business_region.setCurrentText(str(self.recent.value("business_region", "")))
        self.business_region.lineEdit().setPlaceholderText("예: 경기도, 전라북도 (비워 두면 전국 후보)")
        self.output_directory = QLineEdit(str(self.recent.value("output_directory", "")))
        self.drawing = QLineEdit(str(self.recent.value("drawing", "")))
        self.vworld_api_key = QLineEdit(str(self.recent.value("vworld_api_key", "")))
        self.vworld_api_key.setEchoMode(QLineEdit.Password)
        self.vworld_api_key.setPlaceholderText("VWorld API Key 입력 · 비워 두면 환경설정 사용")
        self.data_go_kr_service_key = QLineEdit(
            str(self.recent.value("data_go_kr_service_key", ""))
        )
        self.data_go_kr_service_key.setEchoMode(QLineEdit.Password)
        self.data_go_kr_service_key.setPlaceholderText(
            "공공데이터포털 서비스키 입력 · Encoding/Decoding 모두 가능"
        )
        layout.addRow("사업명", self.project_name)
        layout.addRow("사업 지역 (시·도)", self.business_region)
        layout.addRow("프로젝트 저장 위치", self._path_row(self.output_directory, self._choose_output))
        layout.addRow("DXF 노선도면", self._path_row(self.drawing, self._choose_drawing))
        layout.addRow("VWorld API Key 입력", self.vworld_api_key)
        layout.addRow("공공데이터포털 서비스키 입력", self.data_go_kr_service_key)
        self.drawing.textChanged.connect(self._invalidate_analysis)
        self.business_region.currentTextChanged.connect(self._invalidate_analysis)
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
        from qgis.gui import QgsMapCanvas, QgsMapToolPan

        page = QWizardPage()
        page.setTitle("2. 도면 분석")
        page.setSubTitle(
            "DXF 좌표와 사업 지역으로 좌표계를 추천합니다. 기본도와 도면을 겹쳐 위치를 확인해 주세요."
        )
        layout = QVBoxLayout(page)
        form = QFormLayout()
        self.crs_recommendations = QComboBox()
        self.crs_recommendations.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.crs_recommendations.setMinimumContentsLength(35)
        self.crs_recommendations.activated.connect(self._apply_recommendation)
        form.addRow("추천 좌표계 후보", self.crs_recommendations)
        self.source_crs = self._crs_selector("EPSG:5186")
        form.addRow("원본 도면 좌표계", self.source_crs)
        layout.addLayout(form)
        self.coordinate_report = QLabel("도면 분석 후 실제 X/Y 범위와 도면 단위를 표시합니다.")
        self.coordinate_report.setWordWrap(True)
        self.coordinate_report.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.coordinate_report)
        self.recommendation_status = QLabel("사업 지역을 입력하면 좌표계 후보를 좁힐 수 있습니다.")
        self.recommendation_status.setWordWrap(True)
        layout.addWidget(self.recommendation_status)
        analyze = QPushButton("도면 다시 분석 · 좌표계 추천")
        analyze.clicked.connect(self._analyze)
        self.candidate_table = QTableWidget(0, 4)
        self.candidate_table.setHorizontalHeaderLabels(["CAD 레이어", "객체 유형", "객체 수", "총 길이"])
        self.candidate_table.setSelectionMode(QTableWidget.NoSelection)
        self.candidate_table.setSortingEnabled(False)
        self.candidate_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(analyze)
        tabs = QTabWidget()
        map_page = QWidget()
        map_layout = QVBoxLayout(map_page)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("11_기본도"))
        self.preview_basemap = QComboBox()
        self.preview_basemap.addItem("VWorld Street · 일반지도", "street")
        self.preview_basemap.addItem("VWorld Hybrid · 위성 + 지명", "hybrid")
        controls.addWidget(self.preview_basemap)
        fit_button = QPushButton("도면 전체 보기")
        controls.addWidget(fit_button)
        controls.addStretch()
        map_layout.addLayout(controls)
        self.preview_canvas = QgsMapCanvas(map_page)
        self.preview_canvas.setMinimumHeight(240)
        self._preview = CadMapPreview(self.preview_canvas)
        self._preview_pan = QgsMapToolPan(self.preview_canvas)
        self.preview_canvas.setMapTool(self._preview_pan)
        self.preview_basemap.currentIndexChanged.connect(
            lambda _: self._preview.set_basemap(self.preview_basemap.currentData())
        )
        fit_button.clicked.connect(self._preview.zoom_to_drawing)
        map_layout.addWidget(self.preview_canvas, 1)
        self.preview_status = QLabel("도면 분석 후 붉은 선으로 표시합니다.")
        self.preview_status.setWordWrap(True)
        self.preview_canvas.renderErrorOccurred.connect(self._preview_render_error)
        map_layout.addWidget(self.preview_status)
        map_layout.addWidget(QLabel("드래그: 이동 · 휠: 확대/축소 | 기본도: VWorld / 국토교통부 · 인터넷 연결 필요"))
        tabs.addTab(map_page, "지도에서 좌표 확인")
        tabs.addTab(self.candidate_table, "CAD 객체 목록")
        layout.addWidget(tabs, 1)
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
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("단계 진행률 %p%")
        self.build_stage = QLabel("프로젝트 생성 대기")
        self.build_stage.setWordWrap(True)
        self.build_elapsed = QLabel("생성을 누르면 진행 단계와 처리 로그가 표시됩니다.")
        self.build_elapsed.setWordWrap(True)
        layout.addWidget(self.run_button)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.build_stage)
        layout.addWidget(self.build_elapsed)
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
        self._preview_timer.stop()
        self._preview.clear_drawing()
        self.service.last_coordinate_report = None
        self.service.last_cad_lines = {}
        self.crs_recommendations.clear()
        self.coordinate_report.setText("도면 분석 후 실제 X/Y 범위와 도면 단위를 표시합니다.")
        self.recommendation_status.setText("도면 또는 사업 지역이 변경되었습니다. 다시 분석해 주세요.")
        self.preview_status.setText("도면 분석 후 붉은 선으로 표시합니다.")
        self.candidate_table.setRowCount(0)
        self.cad_status.setText("도면 또는 사업 지역이 변경되었습니다. 다시 분석해 주세요.")

    def initializePage(self, page_id: int) -> None:
        super().initializePage(page_id)
        if page_id == self._cad_page_id and self.project_type is None:
            self._analyze()

    def _analyze(self):
        self._invalidate_analysis()
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
        report = self.service.last_coordinate_report
        min_x, min_y, max_x, max_y = report.bounds
        self.coordinate_report.setText(
            f"DXF 실제 좌표: X {min_x:,.3f} ~ {max_x:,.3f} / Y {min_y:,.3f} ~ {max_y:,.3f}\n"
            f"범위 중심: ({report.center[0]:,.3f}, {report.center[1]:,.3f}) · "
            f"$INSUNITS={report.insunits} ({report.unit_name}) · X=동쪽, Y=북쪽으로 해석"
        )
        self._recommend_coordinates()
        try:
            self._preview.load_lines(self.service.last_cad_lines)
            self._refresh_preview()
        except Exception as exc:
            self.preview_status.setText(f"도면 미리보기 실패: {exc}")

    def _recommend_coordinates(self):
        report = self.service.last_coordinate_report
        try:
            candidates = recommend_crs(report.bounds, self.business_region.currentText())
        except RouteGISError as exc:
            self.recommendation_status.setText(str(exc))
            return
        for index, candidate in enumerate(candidates, start=1):
            self.crs_recommendations.addItem(f"{index}. {candidate.code} — {candidate.name}", candidate.code)
            self.crs_recommendations.setItemData(
                index - 1,
                f"{candidate.reason}\n예상 중심: 경도 {candidate.longitude:.6f}, 위도 {candidate.latitude:.6f}",
                Qt.ToolTipRole,
            )
        if candidates:
            self.recommendation_status.setText(
                f"추천 후보 {len(candidates)}개 · 시·도 대략 범위와 EPSG 사용범위 기준 (동점은 목록 순서). "
                "첫 후보를 미리보기에 임시 적용합니다. Bessel/GRS80 등은 지도에서 비교해 선택하세요."
            )
            self._apply_recommendation(0)
        else:
            self.recommendation_status.setText(
                "해당 지역에 맞는 후보가 없습니다. 사업 지역, 지역 원점·임의 좌표, X/Y 뒤바뀜, "
                "경위도·mm 단위를 확인하거나 원본 좌표계를 직접 선택해 주세요."
            )
        if report.insunits not in (0, 6):
            self.recommendation_status.setText(
                self.recommendation_status.text()
                + " 도면 단위가 미터로 기록되어 있지 않습니다. 좌표 단위를 확인하세요 (자동 배율 변환 없음)."
            )

    def _apply_recommendation(self, index: int):
        code = self.crs_recommendations.itemData(index)
        if code:
            self.source_crs.setCurrentIndex(self.source_crs.findData(code))

    def _schedule_preview(self, *_: object):
        if self._preview.line_layer is not None:
            self._preview_timer.start()

    def _refresh_preview(self):
        self._preview_timer.stop()
        if self._preview.line_layer is None:
            return
        code = self._crs_code(self.source_crs)
        self.crs_recommendations.setCurrentIndex(self.crs_recommendations.findData(code))
        try:
            longitude, latitude = self._preview.set_source_crs(code)
            self.preview_status.setText(
                f"붉은 선: DXF / {code} · 예상 중심 경도 {longitude:.6f}, 위도 {latitude:.6f}\n"
                "도로·하천 등 기준 위치와 맞는지 확인하세요. 기본도가 보이지 않으면 인터넷 연결을 확인해 주세요."
            )
        except Exception as exc:
            self.preview_status.setText(f"좌표계 미리보기 실패: {exc}")

    def _dispose_preview(self, *_: object):
        self._preview_timer.stop()
        self._preview.close()
        self.service.last_cad_lines = {}

    def _preview_render_error(self, message, layer):
        self.preview_status.setText(f"지도 표시 오류 ({layer.name()}): {message}")

    def validateCurrentPage(self) -> bool:
        if self.currentId() == self._cad_page_id and self.project_type is None:
            QMessageBox.warning(self, "도면 분석 필요", "도면 분석을 실행해 주세요.")
            return False
        if self.currentId() == self._cad_page_id:
            try:
                validate_projected_metre_crs(self._crs_code(self.source_crs))
            except RouteGISError as exc:
                QMessageBox.warning(self, "원본 좌표계 확인", str(exc))
                return False
            self._refresh_preview()
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
            checked_ids = set(self._selected_layers())
            added = self.service.catalog.merge_live(definitions)
            self._render_layer_list(checked_ids)
            total = len(self.service.catalog.all())
            self.layer_status.setText(
                f"브이월드 목록 {total}개 · 신규 {added}개 · "
                f"선택 {len(checked_ids)}개"
            )
        finally:
            self._vworld_task = None
            if self._vworld_cursor_active:
                QApplication.restoreOverrideCursor()
                self._vworld_cursor_active = False
            self.load_vworld_button.setEnabled(True)

    def _load_vworld_catalog(self) -> None:
        self._save_credentials()
        api_key = self.vworld_api_key.text().strip() or load_vworld_api_key(PLUGIN_DOTENV)
        if not api_key:
            message = "프로젝트 화면에 VWorld API Key를 입력하거나 플러그인 .env에 설정해 주세요."
            self.layer_status.setText(f"현재 목록 유지 · {message}")
            QMessageBox.warning(self, "브이월드 목록 불러오기", message)
            return

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

    def _set_build_running(self, running: bool) -> None:
        self.run_button.setEnabled(not running)
        self.open_button.setEnabled(not running and self.result is not None)
        for page_id in self.pageIds():
            if page_id != self._result_page_id:
                self.page(page_id).setEnabled(not running)
        for button in (QWizard.BackButton, QWizard.NextButton, QWizard.FinishButton):
            self.button(button).setEnabled(not running)
        self.button(QWizard.CancelButton).setEnabled(True)
        self.button(QWizard.CancelButton).setText("생성 취소" if running else "취소")

    def _append_build_log(self, message: str) -> None:
        elapsed = max(0, int(monotonic() - self._build_started))
        self.progress.moveCursor(QTextCursor.End)
        self.progress.insertPlainText(f"[{elapsed // 60:02d}:{elapsed % 60:02d}] {message}\n")
        self.progress.ensureCursorVisible()

    def _update_build_elapsed(self) -> None:
        if self._build_task is None:
            return
        now = monotonic()
        elapsed = int(now - self._build_started)
        quiet = int(now - self._last_build_update)
        status = "취소 요청 중" if self._build_cancel_requested else "처리 중"
        self.build_elapsed.setText(
            f"{status} · 경과 {elapsed // 60:02d}:{elapsed % 60:02d} · "
            f"마지막 진행 알림 {quiet}초 전"
            + (" · 현재 단계의 완료/서버 응답을 기다리고 있습니다." if quiet >= 30 else "")
        )

    def _on_build_progress(self, percent: int, message: str) -> None:
        if self._build_task is None:
            return
        self._last_build_update = monotonic()
        self.progress_bar.setValue(percent)
        self.build_stage.setText(message)
        self._append_build_log(message)
        self._update_build_elapsed()

    def _cancel_build(self) -> None:
        if self._build_task is None or self._build_cancel_requested:
            return
        self._build_cancel_requested = True
        self._build_task.cancel()
        self.button(QWizard.CancelButton).setEnabled(False)
        self.build_stage.setText("취소 요청됨 · 현재 처리/서버 요청 종료 후 중단합니다.")
        self._append_build_log("취소 요청됨 · 현재 단계가 끝나면 임시 결과를 정리합니다.")
        self._update_build_elapsed()

    def _build(self):
        if self._build_task is not None:
            return
        if self._vworld_task is not None:
            QMessageBox.warning(self, "목록 불러오는 중", "브이월드 목록 불러오기가 끝난 뒤 생성해 주세요.")
            return
        self.result = None
        self._build_started = self._last_build_update = monotonic()
        self._build_cancel_requested = False
        self.progress.clear()
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("단계 진행률 %p%")
        self.build_stage.setText("프로젝트 생성 준비 중")
        self.build_elapsed.setText("처리 중 · 경과 00:00")
        self._set_build_running(True)
        api_key = ""
        data_go_kr_service_key = ""
        try:
            self._save_credentials()
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
                self.vworld_api_key.text().strip()
                or load_vworld_api_key(PLUGIN_DOTENV)
            )
            data_go_kr_service_key = (
                self.data_go_kr_service_key.text().strip()
                or load_data_go_kr_service_key(PLUGIN_DOTENV)
            )
            for label, field, key in (
                ("VWorld", self.vworld_api_key, api_key),
                ("공공데이터포털", self.data_go_kr_service_key, data_go_kr_service_key),
            ):
                source = "입력란(저장값 포함)" if field.text().strip() else "환경변수 / 플러그인 .env"
                self._append_build_log(f"{label} 인증키: {source if key else '미설정'}")
            self._build_task = ProjectBuildTask(
                self.service,
                config,
                api_key=api_key,
                data_go_kr_service_key=data_go_kr_service_key,
            )
            self._build_task.progress_message.connect(self._on_build_progress, Qt.QueuedConnection)
            self._build_task.completed.connect(self._finish_build)
            self._build_timer.start()
            QgsApplication.taskManager().addTask(self._build_task)
        except Exception as exc:
            safe_error = redact_sensitive_text(exc, api_key, data_go_kr_service_key) or "알 수 없는 오류"
            self._finish_build(safe_error)
        finally:
            api_key = ""
            data_go_kr_service_key = ""

    def _finish_build(self, error=None, result=None, cancelled=False) -> None:
        self._build_timer.stop()
        self._build_task = None
        elapsed = max(0, int(monotonic() - self._build_started))
        self.build_elapsed.setText(f"처리 종료 · 경과 {elapsed // 60:02d}:{elapsed % 60:02d}")
        try:
            if cancelled:
                self.build_stage.setText("프로젝트 생성 취소됨")
                self.progress_bar.setFormat("취소됨 · %p%")
                self._append_build_log("프로젝트 생성이 취소되었습니다.")
                return
            if error or result is None:
                safe_error = redact_sensitive_text(
                    error, self.vworld_api_key.text().strip(), self.data_go_kr_service_key.text().strip()
                ) or "작업이 결과를 반환하지 않았습니다."
                self.build_stage.setText(f"생성 실패: {safe_error}")
                self.progress_bar.setFormat("실패 · %p%")
                self._append_build_log(f"오류: {safe_error}")
                QMessageBox.critical(self, "프로젝트 생성 실패", safe_error)
                return
            self.result = result
            self.progress_bar.setValue(100)
            self.recent.setValue("output_directory", self.output_directory.text())
            self.recent.setValue("drawing", self.drawing.text())
            self.recent.setValue("business_region", self.business_region.currentText())
            self.recent.remove("domain")
            self.recent.sync()
            lines = [
                "프로젝트 생성 결과",
            ]
            failed_count = 0
            for item in self.result.downloads:
                definition = self.service.catalog.get(item.layer_id)
                label = f"{definition.name} [{definition.provider} / {item.layer_id}]"
                if item.status == "success":
                    detail = f"성공 · {item.feature_count}개" if item.feature_count else "성공 · 0개 (해당없음)"
                    lines.append(f"✓ {label}: {detail}")
                else:
                    failed_count += 1
                    error = redact_sensitive_text(
                        item.error, self.vworld_api_key.text().strip(), self.data_go_kr_service_key.text().strip()
                    )
                    lines.append(f"⚠ {label}: 실패\n    원인: {error or '공급자가 상세 오류를 반환하지 않았습니다.'}")
            if failed_count:
                total = len(self.result.downloads)
                summary = f"공간정보 {total}개 모두 실패했습니다." if failed_count == total else f"공간정보 {total}개 중 {failed_count}개가 실패했습니다."
                lines.append(f"\n{summary} 위 원인을 확인해 주세요.")
            lines.extend([
                "✓ GeoPackage 생성",
                "✓ SHP 생성",
                "✓ QGIS 프로젝트 생성",
                f"\n{self.result.state.value}",
                f"보고서: {self.result.report_html}",
                f"로그: {self.result.project_directory / 'logs' / 'app.log'}",
            ])
            self._append_build_log("\n".join(lines))
            self.build_stage.setText("생성 완료 · QGIS에서 프로젝트 여는 중")
            self._open_project()
            self.build_stage.setText("생성 완료 · 일부 공간정보 실패 (로그 확인)" if failed_count else "프로젝트 생성 완료")
        finally:
            self._set_build_running(False)

    def detach_build(self) -> None:
        """Let plugin unload safely while an in-flight request finishes."""
        if self._build_task is not None:
            self._build_task.cancel()
            self._build_task.progress_message.disconnect(self._on_build_progress)
            self._build_task.completed.disconnect(self._finish_build)
            self._build_task = None
        self._build_timer.stop()

    def reject(self) -> None:
        if self._build_task is not None:
            self._cancel_build()
            return
        self._save_credentials()
        super().reject()

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
        if self._build_task is not None:
            self._cancel_build()
            event.ignore()
            return
        self._save_credentials()
        if self._vworld_task is not None:
            self._vworld_task.cancel()
        super().closeEvent(event)
