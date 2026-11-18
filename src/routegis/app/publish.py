from __future__ import annotations

import json
import shutil
import tempfile
import time
import uuid
from pathlib import Path

from ..errors import ConfigurationError
from .project_log import _close_project_logger


PROJECT_DIRECTORIES = (
    "source",
    "data",
    "shp/route",
    "shp/cadastral",
    "shp/land",
    "shp/ecology",
    "shp/heritage",
    "shp/water",
    "shp/power",
    "shp/landuse",
    "shp/administrative",
    "shp/facility",
    "shp/source",
    "style",
    "reports",
    "logs",
)


def _directories(project_directory: Path) -> None:
    for relative in PROJECT_DIRECTORIES:
        (project_directory / relative).mkdir(parents=True, exist_ok=True)


def _validate_existing_project(project_directory: Path, project_name: str) -> None:
    if not project_directory.exists():
        return
    if not project_directory.is_dir():
        raise ConfigurationError(f"프로젝트 출력 경로가 폴더가 아닙니다: {project_directory}")
    if not any(project_directory.iterdir()):
        return
    manifest = project_directory / "project_manifest.json"
    if not manifest.is_file():
        raise ConfigurationError(
            f"기존 폴더를 덮어쓸 수 없습니다. RouteGIS 프로젝트가 아닙니다: {project_directory}"
        )
    try:
        existing = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"기존 RouteGIS Manifest를 읽을 수 없습니다: {manifest}") from exc
    if existing.get("project") != project_name:
        raise ConfigurationError(
            f"같은 출력 폴더에 다른 RouteGIS 프로젝트가 있습니다: {project_directory}"
        )


def _staging_directory(project_directory: Path) -> Path:
    project_directory.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{project_directory.name}.build-",
            dir=project_directory.parent,
        )
    )
    try:
        _directories(staging)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return staging


def _publish_project(staging: Path, project_directory: Path) -> None:
    backup: Path | None = None
    new_project = not project_directory.exists()
    operation = "완성된 프로젝트 폴더 저장"
    source, target = staging, project_directory
    try:
        _close_project_logger(project_directory)
        if not new_project:
            backup = project_directory.with_name(f".{project_directory.name}.backup-{uuid.uuid4().hex}")
            operation = "기존 프로젝트 폴더 백업"
            source, target = project_directory, backup
            _replace_directory(project_directory, backup)
        operation = "완성된 프로젝트 폴더 저장"
        source, target = staging, project_directory
        try:
            _replace_directory(staging, project_directory)
        except PermissionError as exc:
            # Windows can deny renaming a directory while a reader scans a new
            # artifact, even though every file is complete and still readable.
            # Never use this fallback when replacing a previous project.
            if not new_project or getattr(exc, "winerror", None) not in {5, 32, 33}:
                raise
            operation = "새 프로젝트 폴더로 완성된 파일 복사"
            _copy_new_project(staging, project_directory)
    except BaseException as exc:
        try:
            if project_directory.exists() and not staging.exists():
                _replace_directory(project_directory, staging)
            if backup is not None and backup.exists() and not project_directory.exists():
                _replace_directory(backup, project_directory)
        except OSError as rollback_error:
            raise ConfigurationError(
                "프로젝트 교체 실패 후 기존 결과를 복구할 수 없습니다.\n"
                f"최초 오류: {_filesystem_error(exc)}\n"
                f"복구 오류: {_filesystem_error(rollback_error)}\n"
                f"기존 결과 백업: {backup}\n임시 결과: {staging}\n최종 위치: {project_directory}"
            ) from rollback_error
        if isinstance(exc, OSError):
            raise ConfigurationError(
                f"프로젝트 최종 저장에 실패했습니다.\n작업: {operation}\n"
                f"원본: {source}\n대상: {target}\n원인: {_filesystem_error(exc)}\n"
                "폴더 변경 권한 또는 다른 프로그램의 파일 사용 여부를 확인해 주세요.\n"
                f"임시 결과 위치: {staging}"
            ) from exc
        raise
    if backup is not None:
        shutil.rmtree(backup, ignore_errors=True)


def _filesystem_error(error: BaseException) -> str:
    codes = ", ".join(
        f"{name}={value}"
        for name in ("winerror", "errno")
        if (value := getattr(error, name, None)) is not None
    )
    return f"{type(error).__name__} ({codes}): {error}" if codes else f"{type(error).__name__}: {error}"


def _copy_new_project(staging: Path, project_directory: Path) -> None:
    # Claim an absent destination exclusively. A concurrent creator's directory
    # must not be overwritten or removed, including if the copy later fails.
    project_directory.mkdir()
    try:
        shutil.copytree(
            staging,
            project_directory,
            dirs_exist_ok=True,
            ignore=lambda directory, _names: ["project_manifest.json"] if Path(directory) == staging else [],
        )
        # An incomplete copy must not look like a reusable RouteGIS project.
        manifest = staging / "project_manifest.json"
        if manifest.is_file():
            shutil.copy2(manifest, project_directory / manifest.name)
    except BaseException:
        shutil.rmtree(project_directory, ignore_errors=True)
        raise
    shutil.rmtree(staging, ignore_errors=True)


def _replace_directory(source: Path, target: Path) -> None:
    for attempt in range(5):
        try:
            source.replace(target)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.1 * (2**attempt))
