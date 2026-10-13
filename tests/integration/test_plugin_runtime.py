"""Exercise the real plugin worker with an incompatible global core already loaded."""

from __future__ import annotations

import importlib
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pytest


def _assert_plugin_build(project_root, plugin_parent, package, output_directory):
    from routegis.app.project_service import ProjectService as CachedService

    # Match the pre-0.2.6 signature left in sys.modules after a QGIS upgrade.
    def build(self, config, *, api_key="", data_go_kr_service_key="", domain=None, provider=None):
        raise AssertionError("The plugin used the cached global core")

    setattr(CachedService, "build", build)
    cached_modules = {name: module for name, module in sys.modules.items() if name == "routegis" or name.startswith("routegis.")}

    class Task:
        CanCancel = 1

        def __init__(self, *_args):
            self.progress = []

        def isCanceled(self):
            return False

        def setProgress(self, percent):
            self.progress.append(percent)

    # Only Qt/QGIS are stubbed; core imports, task execution and output are real.
    for name in ("qgis", "qgis.PyQt", "qgis.PyQt.QtCore", "qgis.PyQt.QtGui", "qgis.PyQt.QtWidgets", "qgis.core"):
        module = ModuleType(name)
        module.__getattr__ = lambda name: Mock()
        sys.modules[name] = module
    setattr(sys.modules["qgis.PyQt.QtWidgets"], "QWizard", type("QWizard", (), {}))
    setattr(sys.modules["qgis.core"], "QgsTask", Task)
    sys.path.insert(0, plugin_parent)
    wizard = importlib.import_module(f"{package}.wizard")
    fixture_provider = importlib.import_module(f"{package}.routegis.cli").FixtureProvider
    config = wizard.ProjectConfig(
        project_name="Upgrade regression",
        source_drawing=Path(project_root) / "tests" / "fixtures" / "sample_route.dxf",
        source_layers=(), source_crs="EPSG:5186", output_crs="EPSG:5186",
        buffer_distance=1000, selected_layers=("land_ownership",),
        output_directory=Path(output_directory), project_type="non_linear",
    )
    service = wizard.ProjectService()
    real_build = service.build
    fixture = fixture_provider(Path(project_root) / "tests" / "fixtures" / "spatial_mock.json")
    service.build = lambda config, **kwargs: real_build(config, provider=fixture, **kwargs)
    task = wizard.ProjectBuildTask(service, config, api_key="", data_go_kr_service_key="")

    assert task.run(), task.error
    assert task.result.project_file.is_file()
    assert task.result.geopackage.is_file()
    assert task.result.downloads[0].status == "success"
    assert task.progress[0] == 0 and task.progress[-1] == 99
    assert type(service).__module__ == f"{package}.routegis.app.project_service"
    assert CachedService.build is build
    assert all(sys.modules[name] is module for name, module in cached_modules.items())


@pytest.mark.parametrize("packaged", [False, True], ids=["source", "packaged"])
def test_plugin_build_is_isolated_from_cached_global_core(tmp_path, packaged):
    project_root = Path(__file__).resolve().parents[2]
    package = "routegis_autobuilder" if packaged else "plugin"
    parent = tmp_path if packaged else project_root
    if packaged:
        ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".env")
        shutil.copytree(project_root / "plugin", parent / package, ignore=ignore)
        shutil.copytree(project_root / "src" / "routegis", parent / package / "routegis", ignore=ignore)
    result = subprocess.run(
        [
            sys.executable, "-c",
            "import runpy, sys; runpy.run_path(sys.argv[1])['_assert_plugin_build'](*sys.argv[2:])",
            str(Path(__file__).resolve()), str(project_root), str(parent), package, str(tmp_path / "output"),
        ],
        cwd=project_root, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
