from __future__ import annotations

from pathlib import Path

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QMessageBox

from .routegis.errors import ConfigurationError

from .wizard import RouteGISWizard


class RouteGISPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.wizard = None

    def initGui(self):
        icon = QIcon(str(Path(__file__).resolve().with_name("icon.png")))
        self.action = QAction(icon, "RouteGIS AutoBuilder", self.iface.mainWindow())
        self.action.setToolTip("노선도면으로 사업분석용 QGIS 프로젝트를 만듭니다.")
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToVectorMenu("RouteGIS AutoBuilder", self.action)

    def unload(self):
        if self.wizard is not None:
            self.wizard.detach_build()
            self.wizard.close()
            self.wizard = None
        if self.action is not None:
            self.iface.removeToolBarIcon(self.action)
            self.iface.removePluginVectorMenu("RouteGIS AutoBuilder", self.action)
            self.action.deleteLater()
            self.action = None

    def run(self):
        if self.wizard is None or not self.wizard.isVisible():
            if self.wizard is not None:
                self.wizard.deleteLater()
            try:
                self.wizard = RouteGISWizard(self.iface, self.iface.mainWindow())
            except ConfigurationError as exc:
                self.wizard = None
                QMessageBox.critical(self.iface.mainWindow(), "RouteGIS 설정 오류", str(exc))
                return
        self.wizard.show()
        self.wizard.raise_()
        self.wizard.activateWindow()
