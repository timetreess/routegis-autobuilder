from __future__ import annotations

from qgis.PyQt.QtCore import pyqtSignal
from qgis.core import QgsTask

from .routegis.security import redact_sensitive_text
from .routegis.app.project_service import ProjectService
from .routegis.gis.crs_recommendation import recommend_crs


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

    def _report(self, percent: int, message: str) -> None:
        if self.isCanceled():
            raise _BuildCancelled()
        self.setProgress(percent * 80 // 100)
        self.progress_message.emit(percent * 80 // 100, message)

    def run(self) -> bool:
        try:
            # Keep worker state separate from the Wizard; cancelled/obsolete
            # results must never overwrite its current drawing.
            service = ProjectService(self.settings)
            candidates = service.analyze_drawing(self.drawing, on_progress=self._report)
            self._report(100, "사업 지역과 실제 DXF 좌표로 좌표계 후보 찾는 중")
            report = service.last_coordinate_report
            recommendations = recommend_crs(report.bounds, self.region) if report else []
            self._report(100, "좌표계 후보 준비 완료 · 지도 미리보기 준비 중")
            self.result = {
                "candidates": candidates, "report": report,
                "lines": service.last_cad_lines, "warnings": service.last_cad_warnings,
                "recommendations": recommendations,
            }
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

    def _report(self, percent: int, message: str) -> None:
        if self.isCanceled():
            raise _BuildCancelled()
        self.setProgress(percent)
        self.progress_message.emit(percent, redact_sensitive_text(message, *self._keys) or "")

    def run(self) -> bool:
        try:
            self.result = self.service.build(
                self.config,
                api_key=self._keys[0],
                data_go_kr_service_key=self._keys[1],
                domain=None,
                on_progress=self._report,
                use_native_project=False,
            )
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
