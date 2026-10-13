from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .routegis_plugin import RouteGISPlugin

MINIMUM_PYTHON_VERSION = (3, 9)


def _assert_supported_python(version_info=sys.version_info) -> None:
    if version_info[:2] < MINIMUM_PYTHON_VERSION:
        raise RuntimeError("RouteGIS AutoBuilder requires Python 3.9 or later.")


_assert_supported_python()


class QgisInterface(Protocol):
    def mainWindow(self) -> PluginWindow: ...

    def addToolBarIcon(self, action: PluginAction) -> None: ...

    def addPluginToVectorMenu(self, name: str, action: PluginAction) -> None: ...

    def removeToolBarIcon(self, action: PluginAction) -> None: ...

    def removePluginVectorMenu(self, name: str, action: PluginAction) -> None: ...

    def addProject(self, path: str) -> bool: ...


class PluginWindow(Protocol):
    pass


class PluginAction(Protocol):
    pass

PLUGIN_DIRECTORY = Path(__file__).resolve().parent
VENDOR_DIRECTORY = PLUGIN_DIRECTORY / "_vendor"
if VENDOR_DIRECTORY.is_dir() and str(VENDOR_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(VENDOR_DIRECTORY))

# Keep the core below the plugin namespace so QGIS unloads it on upgrade.
# Source checkouts use the same relative imports as the packaged plugin.
if not (PLUGIN_DIRECTORY / "routegis").is_dir():
    __path__.append(str(PLUGIN_DIRECTORY.parent / "src"))


def classFactory(iface: QgisInterface) -> RouteGISPlugin:
    from .routegis_plugin import RouteGISPlugin

    return RouteGISPlugin(iface)
