import os
from pathlib import Path

import pytest

from routegis.environment import load_data_go_kr_service_key, load_vworld_api_key


def test_environment_variable_has_priority(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    dotenv = tmp_path / ".env"
    dotenv.write_text("VWORLD_API_KEY=file-key\n", encoding="utf-8")
    monkeypatch.setenv("VWORLD_API_KEY", "system-key")

    assert load_vworld_api_key(dotenv) == "system-key"


def test_first_dotenv_value_is_loaded_without_mutating_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    first = tmp_path / "plugin.env"
    second = tmp_path / "project.env"
    first.write_text("# local plugin secret\nexport VWORLD_API_KEY='plugin-key'\n", encoding="utf-8")
    second.write_text('VWORLD_API_KEY="project-key"\n', encoding="utf-8")
    monkeypatch.delenv("VWORLD_API_KEY", raising=False)

    assert load_vworld_api_key(first, second) == "plugin-key"
    assert "VWORLD_API_KEY" not in os.environ


def test_blank_or_missing_dotenv_value_returns_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    dotenv = tmp_path / ".env"
    dotenv.write_text("VWORLD_API_KEY=\n", encoding="utf-8")
    monkeypatch.delenv("VWORLD_API_KEY", raising=False)

    assert load_vworld_api_key(tmp_path / "missing.env", dotenv) == ""


def test_data_go_kr_environment_variable_has_priority(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    dotenv = tmp_path / ".env"
    dotenv.write_text("DATA_GO_KR_SERVICE_KEY=file-key\n", encoding="utf-8")
    monkeypatch.setenv("DATA_GO_KR_SERVICE_KEY", "system-key")

    assert load_data_go_kr_service_key(dotenv) == "system-key"


def test_first_data_go_kr_dotenv_value_is_loaded_without_mutating_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    first = tmp_path / "plugin.env"
    second = tmp_path / "project.env"
    first.write_text("# local plugin secret\nexport DATA_GO_KR_SERVICE_KEY='plugin-key'\n", encoding="utf-8")
    second.write_text('DATA_GO_KR_SERVICE_KEY="project-key"\n', encoding="utf-8")
    monkeypatch.delenv("DATA_GO_KR_SERVICE_KEY", raising=False)

    assert load_data_go_kr_service_key(first, second) == "plugin-key"
    assert "DATA_GO_KR_SERVICE_KEY" not in os.environ


def test_blank_or_missing_data_go_kr_dotenv_value_returns_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    dotenv = tmp_path / ".env"
    dotenv.write_text("DATA_GO_KR_SERVICE_KEY=\n", encoding="utf-8")
    monkeypatch.delenv("DATA_GO_KR_SERVICE_KEY", raising=False)

    assert load_data_go_kr_service_key(tmp_path / "missing.env", dotenv) == ""
