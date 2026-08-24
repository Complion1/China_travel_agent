"""Unit coverage for BaiduMapSettings and its fail-closed loader."""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError
from types import SimpleNamespace

import app.baidu_map.settings as settings_module
from app.baidu_map import BaiduMapConfigurationError, BaiduMapSettings, load_baidu_map_settings


def test_explicit_mapping_is_valid_and_does_not_read_process_or_dotenv(monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise AssertionError("explicit mapping must not read process env or dotenv")

    monkeypatch.setattr(settings_module, "load_project_env", fail)
    monkeypatch.setattr(settings_module, "os", SimpleNamespace(environ=fail))
    settings = load_baidu_map_settings({"BAIDU_MAPS_API_KEY": "  fake-baidu-key  "})
    assert isinstance(settings.api_key, SecretStr)
    assert settings.api_key.get_secret_value() == "fake-baidu-key"


@pytest.mark.parametrize(
    "value",
    [None, "", "   ", "changeme", "CHANGEME", "your-baidu-maps-api-key", "<YOUR-BAIDU-MAPS-API-KEY>", "__BAIDU_MAPS_API_KEY_REQUIRED__"],
)
def test_missing_blank_and_all_placeholders_fail(value) -> None:
    mapping = {} if value is None else {"BAIDU_MAPS_API_KEY": value}
    with pytest.raises(BaiduMapConfigurationError):
        load_baidu_map_settings(mapping)


@pytest.mark.parametrize("legacy_value", ["", "https://example.invalid/mcp"])
def test_legacy_url_key_always_fails(legacy_value: str) -> None:
    with pytest.raises(BaiduMapConfigurationError) as exc_info:
        load_baidu_map_settings(
            {"BAIDU_MAPS_API_KEY": "fake-baidu-key", "BAIDU_MAPS_MCP_URL": legacy_value}
        )
    assert "fake-baidu-key" not in repr(exc_info.value)
    assert "example.invalid" not in repr(exc_info.value)


def test_fixed_dotenv_path_and_process_precedence(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("BAIDU_MAPS_API_KEY=fake-from-dotenv\n", encoding="utf-8")
    monkeypatch.setattr(settings_module, "_PROJECT_ENV_FILE", env_file)
    monkeypatch.delenv("BAIDU_MAPS_API_KEY", raising=False)
    assert load_baidu_map_settings().api_key.get_secret_value() == "fake-from-dotenv"

    env_file.write_text("BAIDU_MAPS_API_KEY=fake-from-dotenv\n", encoding="utf-8")
    monkeypatch.setenv("BAIDU_MAPS_API_KEY", "fake-from-process")
    assert load_baidu_map_settings().api_key.get_secret_value() == "fake-from-process"


def test_dotenv_does_not_interpolate_and_legacy_env_is_rejected(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "BAIDU_MAPS_API_KEY=fake-$UNEXPANDED\nUNEXPANDED=wrong\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings_module, "_PROJECT_ENV_FILE", env_file)
    monkeypatch.delenv("BAIDU_MAPS_API_KEY", raising=False)
    monkeypatch.delenv("BAIDU_MAPS_MCP_URL", raising=False)
    assert load_baidu_map_settings().api_key.get_secret_value() == "fake-$UNEXPANDED"

    monkeypatch.setenv("BAIDU_MAPS_MCP_URL", "")
    with pytest.raises(BaiduMapConfigurationError):
        load_baidu_map_settings()


def test_settings_is_strict_frozen_and_exactly_redacted() -> None:
    settings = BaiduMapSettings(api_key=SecretStr("fake-baidu-key"))
    assert repr(settings) == "BaiduMapSettings(api_key=**********)"
    assert str(settings) == repr(settings)
    assert "fake-baidu-key" not in repr(settings)
    assert settings.model_config["extra"] == "forbid"
    assert settings.model_config["frozen"] is True
    assert settings.model_config["strict"] is True
    with pytest.raises(ValidationError):
        settings.api_key = SecretStr("other")
    with pytest.raises(ValidationError):
        BaiduMapSettings(api_key=SecretStr("fake"), extra="x")
