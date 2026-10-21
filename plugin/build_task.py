from __future__ import annotations

from threading import Event

from qgis.PyQt.QtCore import pyqtSignal
from qgis.core import QgsTask

from .routegis.security import redact_sensitive_text
from .routegis.app.project_service import ProjectService
from .routegis.gis.crs_recommendation import recommend_crs
from .routegis.threading_compat import run_in_python_thread


class _BuildCancelled(Exception):
    pass


class DrawingAnalysisTask(QgsTask):
    progress_message = pyqtSignal(int, str)
    completed = pyqtSignal(object, object, bool)

    def __init__(self, settings, drawing, region: str):
        super().__init__("RouteGIS 도면 분석", QgsTask.CanCancel)
        self.settings = settings
        self.drawing = drawing
        self.region = region
        self.result = None
        self.error = None
        self._cancel_requested = Event()

    def cancel(self) -> None:
        self._cancel_requested.set()
        super().cancel()

    def _report(self, percent: int, message: str) -> None:
        # Only the supervising QgsTask thread touches Qt/QGIS objects.
        self.setProgress(percent)
        self.progress_message.emit(percent, message)

    def _analyze(self, report) -> dict:
        def update(percent: int, message: str) -> None:
            if self._cancel_requested.is_set():
                raise _BuildCancelled()
            report(percent * 80 // 100, message)

        if self._cancel_requested.is_set():
            raise _BuildCancelled()
        # Both DXF analysis and CRS recommendations can call pyproj.
        service = ProjectService(self.settings)
        candidates = service.analyze_drawing(self.drawing, on_progress=update)
        update(100, "사업 지역과 실제 DXF 좌표로 좌표계 후보 찾는 중")
        coordinate_report = service.last_coordinate_report
        recommendations = recommend_crs(coordinate_report.bounds, self.region) if coordinate_report else []
        update(100, "좌표계 후보 준비 완료 · 지도 미리보기 준비 중")
        return {
            "candidates": candidates, "report": coordinate_report,
            "lines": service.last_cad_lines, "warnings": service.last_cad_warnings,
            "recommendations": recommendations,
        }

    def run(self) -> bool:
        try:
            # Qt's QgsTask worker supervises a Python-managed thread to avoid
            # pyproj 3.7.x PROJ context crashes in Qt-created threads on Windows.
            self.result = run_in_python_thread(self._analyze, self._report)
            return True
        except _BuildCancelled:
            return False
        except Exception as exc:
            self.error = redact_sensitive_text(exc) or type(exc).__name__
            return False

    def finished(self, success: bool) -> None:
        self.completed.emit(self.error, self.result, self.isCanceled())


class ProjectBuildTask(QgsTask):
    progress_message = pyqtSignal(int, str)
    completed = pyqtSignal(object, object, bool)

    def __init__(self, service, config, *, api_key: str, data_go_kr_service_key: str):
        super().__init__("RouteGIS 프로젝트 생성", QgsTask.CanCancel)
        self.service = service
        self.config = config
        self._keys: tuple[str, ...] = (api_key, data_go_kr_service_key)
        self.result = None
        self.error = None
        self._cancel_requested = Event()

    def cancel(self) -> None:
        self._cancel_requested.set()
        super().cancel()

    def _check_cancelled(self) -> None:
        if self._cancel_requested.is_set():
            raise _BuildCancelled()

    def _report(self, percent: int, message: str) -> None:
        # Runs on the supervising QgsTask thread; messages are already redacted.
        self.setProgress(percent)
        self.progress_message.emit(percent, message)

    def _build(self, report):
        self._check_cancelled()

        def update(percent: int, message: str) -> None:
            self._check_cancelled()
            report(percent, redact_sensitive_text(message, *self._keys) or "")

        return self.service.build(
            self.config,
            api_key=self._keys[0],
            data_go_kr_service_key=self._keys[1],
            domain=None,
            on_progress=update,
            use_native_project=False,
            check_cancelled=self._check_cancelled,
        )

    def run(self) -> bool:
        try:
            self.result = run_in_python_thread(self._build, self._report)
            for item in self.result.downloads:
                item.error = redact_sensitive_text(item.error, *self._keys)
            return True
        except _BuildCancelled:
            return False
        except Exception as exc:
            self.error = redact_sensitive_text(exc, *self._keys) or type(exc).__name__
            return False
        finally:
            self._keys = ()

    def finished(self, success: bool) -> None:
        # QgsTask calls finished on the main thread. Signals disconnect when the
        # receiving Wizard is destroyed; the worker never holds UI objects.
        self.completed.emit(self.error, self.result, self.result is None and self.isCanceled())
