from __future__ import annotations

import errno
import json
import os
from pathlib import Path

import pytest

from routegis.app import project_service as module
from routegis.app.project_service import ProjectService
from routegis.errors import ConfigurationError
from routegis.models import ProjectConfig


def _windows_lock(path: Path, code: int = 5) -> PermissionError:
    error = PermissionError(errno.EACCES, "directory rename denied", str(path))
    error.winerror = code
    return error


@pytest.fixture
def staged_project(tmp_path, monkeypatch):
    staging = tmp_path / ".project.build-test"
    staging.mkdir()
    (staging / "data").mkdir()
    (staging / "data/project.gpkg").write_bytes(b"complete data")
    (staging / "project.html").write_text("complete map", encoding="utf-8")
    (staging / "project_manifest.json").write_text('{"project":"project"}', encoding="utf-8")
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    return staging, tmp_path / "project"


@pytest.mark.parametrize("winerror", [5, 32, 33])
def test_first_publish_copies_complete_project_when_windows_denies_directory_rename(
    staged_project, monkeypatch, winerror,
):
    staging, target = staged_project
    original_replace = Path.replace

    def locked_replace(source, destination):
        if source == staging:
            raise _windows_lock(source, winerror)
        return original_replace(source, destination)

    monkeypatch.setattr(Path, "replace", locked_replace)
    module._publish_project(staging, target)

    assert (target / "data/project.gpkg").read_bytes() == b"complete data"
    assert (target / "project.html").read_text(encoding="utf-8") == "complete map"
    assert json.loads((target / "project_manifest.json").read_text(encoding="utf-8")) == {"project": "project"}


def test_existing_result_lock_never_falls_back_to_copying_over_existing_files(staged_project, monkeypatch):
    staging, target = staged_project
    target.mkdir()
    (target / "old.txt").write_text("old", encoding="utf-8")
    original_replace = Path.replace

    def locked_replace(source, destination):
        if source == target:
            raise _windows_lock(source)
        return original_replace(source, destination)

    def forbidden_copy(*_args, **_kwargs):
        pytest.fail("Existing results must never be overwritten by the new-project fallback")

    monkeypatch.setattr(Path, "replace", locked_replace)
    monkeypatch.setattr(module.shutil, "copytree", forbidden_copy)
    with pytest.raises(ConfigurationError) as caught:
        module._publish_project(staging, target)

    assert (target / "old.txt").read_text(encoding="utf-8") == "old"
    assert staging.is_dir()
    assert "PermissionError" in str(caught.value)
    assert "winerror=5" in str(caught.value)
    assert str(target) in str(caught.value)
    assert str(staging) in str(caught.value)


def test_other_filesystem_errors_report_the_real_cause_without_copy_fallback(staged_project, monkeypatch):
    staging, target = staged_project
    original_replace = Path.replace

    def missing_source(source, destination):
        if source == staging:
            raise FileNotFoundError(errno.ENOENT, "missing source", str(source))
        return original_replace(source, destination)

    def forbidden_copy(*_args, **_kwargs):
        pytest.fail("Unrelated filesystem errors must not trigger a copy")

    monkeypatch.setattr(Path, "replace", missing_source)
    monkeypatch.setattr(module.shutil, "copytree", forbidden_copy)
    with pytest.raises(ConfigurationError) as caught:
        module._publish_project(staging, target)

    message = str(caught.value)
    assert "FileNotFoundError" in message and "missing source" in message
    assert str(staging) in message and str(target) in message
    assert "QGIS에서 해당 프로젝트와 레이어를 닫은 뒤" not in message


def test_locked_new_artifact_on_rerun_restores_previous_project_without_copying(staged_project, monkeypatch):
    staging, target = staged_project
    target.mkdir()
    (target / "old.txt").write_text("old", encoding="utf-8")
    original_replace = Path.replace

    def locked_replace(source, destination):
        if source == staging:
            raise _windows_lock(source)
        return original_replace(source, destination)

    def forbidden_copy(*_args, **_kwargs):
        pytest.fail("Even after moving the backup, this is not a first-time publish")

    monkeypatch.setattr(Path, "replace", locked_replace)
    monkeypatch.setattr(module.shutil, "copytree", forbidden_copy)
    with pytest.raises(ConfigurationError):
        module._publish_project(staging, target)

    assert (target / "old.txt").read_text(encoding="utf-8") == "old"
    assert not (target / "project_manifest.json").exists()
    assert (staging / "data/project.gpkg").is_file()
    assert not list(target.parent.glob(".project.backup-*"))


def test_copy_fallback_publishes_manifest_only_after_all_other_files(staged_project, monkeypatch):
    staging, target = staged_project
    original_copy = module.shutil.copy2

    def locked_replace(source, destination):
        raise _windows_lock(source)

    def copy_manifest_last(source, destination):
        assert Path(source) == staging / "project_manifest.json"
        assert not Path(destination).exists()
        assert (target / "data/project.gpkg").read_bytes() == b"complete data"
        assert (target / "project.html").read_text(encoding="utf-8") == "complete map"
        return original_copy(source, destination)

    monkeypatch.setattr(Path, "replace", locked_replace)
    monkeypatch.setattr(module.shutil, "copy2", copy_manifest_last)
    module._publish_project(staging, target)
    assert (target / "project_manifest.json").is_file()


def test_copy_fallback_does_not_overwrite_a_destination_created_during_publish(staged_project, monkeypatch):
    staging, target = staged_project
    attempts = 0

    def competing_replace(source, destination):
        nonlocal attempts
        assert source == staging
        attempts += 1
        if attempts == 5:
            target.mkdir()
            (target / "unrelated.txt").write_text("do not touch", encoding="utf-8")
        raise _windows_lock(source)

    monkeypatch.setattr(Path, "replace", competing_replace)
    with pytest.raises(ConfigurationError):
        module._publish_project(staging, target)

    assert (target / "unrelated.txt").read_text(encoding="utf-8") == "do not touch"
    assert not (target / "project_manifest.json").exists()
    assert (staging / "data/project.gpkg").is_file()


def test_failed_copy_removes_only_its_partial_destination_and_keeps_complete_source(staged_project, monkeypatch):
    staging, target = staged_project

    def locked_replace(source, destination):
        raise _windows_lock(source)

    def failed_copy(source, destination, **kwargs):
        assert Path(destination) == target and target.is_dir()
        assert "project_manifest.json" in kwargs["ignore"](str(staging), os.listdir(staging))
        (target / "partial.txt").write_text("partial", encoding="utf-8")
        raise OSError(errno.ENOSPC, "disk full")

    monkeypatch.setattr(Path, "replace", locked_replace)
    monkeypatch.setattr(module.shutil, "copytree", failed_copy)
    with pytest.raises(ConfigurationError, match="disk full"):
        module._publish_project(staging, target)

    assert not target.exists()
    assert (staging / "data/project.gpkg").read_bytes() == b"complete data"
    assert (staging / "project_manifest.json").is_file()


@pytest.fixture
def config(tmp_path, fixture_directory):
    return ProjectConfig(
        project_name="Publish", source_drawing=fixture_directory / "sample_route.dxf",
        source_layers=(), source_crs="EPSG:5186", output_crs="EPSG:5186",
        buffer_distance=1000, selected_layers=(), output_directory=tmp_path,
        project_type="non_linear",
    )


@pytest.mark.skipif(os.name != "nt", reason="Uses a real Windows file handle without FILE_SHARE_DELETE")
def test_first_build_succeeds_while_the_new_html_is_open_for_reading(config):
    streams = []

    def read_new_html(percent, _message):
        if percent == 99:
            staging = next(config.output_directory.glob(".Publish.build-*"))
            streams.append((staging / "Publish.html").open("rb"))

    try:
        result = ProjectService().build(config, use_native_project=False, on_progress=read_new_html)
        assert result.project_file.is_file()
        assert result.geopackage.is_file()
        assert result.web_map_html.read_bytes() == streams[0].read()
        assert result.manifest.is_file()
    finally:
        for stream in streams:
            stream.close()


def test_publish_failure_preserves_completed_outputs_and_redacted_diagnostics(config, monkeypatch):
    original_replace = Path.replace

    def failed_publish(source, destination):
        if source.name.startswith(".Publish.build-"):
            raise OSError(errno.EIO, "fixture-secret storage failure", str(source))
        return original_replace(source, destination)

    monkeypatch.setattr(Path, "replace", failed_publish)
    with pytest.raises(ConfigurationError):
        ProjectService().build(config, api_key="fixture-secret", use_native_project=False)

    staging = next(config.output_directory.glob(".Publish.build-*"))
    for relative in ("Publish.qgz", "Publish.html", "data/project.gpkg", "project_manifest.json"):
        assert (staging / relative).is_file()
    log = (staging / "logs/app.log").read_text(encoding="utf-8")
    assert "storage failure" in log and "OSError" in log
    assert "fixture-secret" not in log
    assert not (config.output_directory / "Publish").exists()
