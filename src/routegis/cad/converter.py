from __future__ import annotations

import subprocess
from pathlib import Path

from ..errors import CadReadError


class DwgConverter:
    """Adapter for converters accepting: executable input.dwg output.dxf."""

    def __init__(self, executable: Path):
        self.executable = Path(executable)

    def convert(self, source: Path, destination: Path) -> Path:
        if not self.executable.is_file():
            raise CadReadError(f"DWG 변환기를 찾을 수 없습니다: {self.executable}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            completed = subprocess.run(
                [str(self.executable), str(source), str(destination)],
                capture_output=True,
                text=True,
                check=False,
                timeout=300,
            )
        except subprocess.TimeoutExpired as exc:
            raise CadReadError("DWG 변환 시간이 5분을 초과했습니다.") from exc
        if completed.returncode != 0 or not destination.is_file():
            detail = (completed.stderr or completed.stdout or "알 수 없는 오류").strip()
            raise CadReadError(f"DWG를 DXF로 변환하지 못했습니다: {detail[:300]}")
        return destination
