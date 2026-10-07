import subprocess
from pathlib import Path

import pytest

from routegis.cad.converter import DwgConverter
from routegis.errors import CadReadError


def _converter_inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    executable = tmp_path / "converter.exe"
    executable.write_bytes(b"stub")
    source = tmp_path / "route.dwg"
    source.write_bytes(b"dwg")
    return executable, source, tmp_path / "route.dxf"


def test_dwg_converter_returns_created_dxf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    executable, source, destination = _converter_inputs(tmp_path)

    def run_success(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        Path(command[2]).write_text("DXF", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run_success)

    assert DwgConverter(executable).convert(source, destination) == destination
    assert destination.read_text(encoding="utf-8") == "DXF"


@pytest.mark.parametrize(
    ("returncode", "create_output", "expected"),
    [
        (2, False, "license error"),
        (0, False, "알 수 없는 오류"),
    ],
)
def test_dwg_converter_rejects_failed_or_missing_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
    create_output: bool,
    expected: str,
):
    executable, source, destination = _converter_inputs(tmp_path)

    def run_failure(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        if create_output:
            Path(command[2]).write_text("DXF", encoding="utf-8")
        return subprocess.CompletedProcess(command, returncode, "", "license error" if returncode else "")

    monkeypatch.setattr(subprocess, "run", run_failure)

    with pytest.raises(CadReadError, match=expected):
        DwgConverter(executable).convert(source, destination)


def test_dwg_converter_reports_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    executable, source, destination = _converter_inputs(tmp_path)

    def run_timeout(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(command, 300)

    monkeypatch.setattr(subprocess, "run", run_timeout)

    with pytest.raises(CadReadError, match="5분"):
        DwgConverter(executable).convert(source, destination)
