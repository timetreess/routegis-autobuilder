from __future__ import annotations

import os
from pathlib import Path


def _dotenv_value(path: Path, name: str) -> str:
    if not path.is_file():
        return ""
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, raw_value = line.partition("=")
        if not separator or key.strip() != name:
            continue
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        return value.strip()
    return ""


def load_vworld_api_key(*dotenv_paths: Path) -> str:
    configured = os.getenv("VWORLD_API_KEY", "").strip()
    if configured:
        return configured
    for path in dotenv_paths:
        configured = _dotenv_value(Path(path), "VWORLD_API_KEY")
        if configured:
            return configured
    return ""


def load_data_go_kr_service_key(*dotenv_paths: Path) -> str:
    configured = os.getenv("DATA_GO_KR_SERVICE_KEY", "").strip()
    if configured:
        return configured
    for path in dotenv_paths:
        configured = _dotenv_value(Path(path), "DATA_GO_KR_SERVICE_KEY")
        if configured:
            return configured
    return ""
